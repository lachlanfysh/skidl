"""JLCPCB/LCSC part lookup for footprint resolution and BOM generation.

Two lookup strategies:
  1. Offline: basic/preferred CSV for common passives (fast, no network)
  2. Online: jlcsearch.tscircuit.com API for everything else (free, no auth)

Usage:
    from corpus.jlc.lookup import JLCLookup
    jlc = JLCLookup()
    results = jlc.search("100nF 0603 capacitor")
    # -> [JLCPart(lcsc="C14663", mfr="...", package="0603", ...)]
"""

from __future__ import annotations

import csv
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import httpx

BASIC_CSV = Path(__file__).parent / "jlcpcb-basic-preferred.csv"
JLCSEARCH_API = "https://jlcsearch.tscircuit.com"
CACHE_DIR = Path(__file__).parent / "cache"


@dataclass
class JLCPart:
    lcsc: str
    mfr: str = ""
    package: str = ""
    description: str = ""
    manufacturer: str = ""
    stock: int = 0
    price: float = 0.0
    basic: bool = False
    joints: int = 0

    @property
    def pad_count(self) -> int:
        return self.joints


class JLCLookup:
    """Search JLCPCB parts database."""

    def __init__(self, use_api: bool = True, cache: bool = True):
        self.use_api = use_api
        self._cache_enabled = cache
        self._basic_parts: list[dict] | None = None
        self._api_cache: dict[str, list[JLCPart]] = {}
        if cache:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def _load_basic(self) -> list[dict]:
        if self._basic_parts is not None:
            return self._basic_parts
        if not BASIC_CSV.exists():
            self._basic_parts = []
            return []
        with open(BASIC_CSV, encoding="utf-8", errors="replace") as f:
            self._basic_parts = list(csv.DictReader(f))
        return self._basic_parts

    def search_basic(self, query: str, limit: int = 5) -> list[JLCPart]:
        """Search basic/preferred parts CSV (offline)."""
        parts = self._load_basic()
        query_lower = query.lower()
        terms = query_lower.split()

        scored: list[tuple[int, dict]] = []
        for p in parts:
            text = f"{p.get('mfr','')} {p.get('package','')} {p.get('description','')}".lower()
            score = sum(1 for t in terms if t in text)
            if score >= max(1, len(terms) // 2):
                scored.append((score, p))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            JLCPart(
                lcsc=f"C{p.get('lcsc', '')}",
                mfr=p.get("mfr", ""),
                package=p.get("package", ""),
                description=p.get("description", ""),
                manufacturer=p.get("manufacturer", ""),
                stock=int(p.get("stock", 0) or 0),
                price=_first_price(p.get("price", "")),
                basic=p.get("basic") == "1",
                joints=int(p.get("joints", 0) or 0),
            )
            for _, p in scored[:limit]
        ]

    def search_api(self, query: str, limit: int = 5) -> list[JLCPart]:
        """Search via jlcsearch.tscircuit.com API (online)."""
        if query in self._api_cache:
            return self._api_cache[query][:limit]

        cache_file = CACHE_DIR / f"{_cache_key(query)}.json" if self._cache_enabled else None
        if cache_file and cache_file.exists():
            try:
                data = json.loads(cache_file.read_text())
                parts = [JLCPart(**p) for p in data]
                self._api_cache[query] = parts
                return parts[:limit]
            except (json.JSONDecodeError, TypeError):
                pass

        try:
            resp = httpx.get(
                f"{JLCSEARCH_API}/api/search",
                params={"q": query, "limit": limit},
                timeout=10.0,
            )
            resp.raise_for_status()
            items = resp.json()
        except (httpx.HTTPError, json.JSONDecodeError):
            return []

        if not isinstance(items, list):
            items = items.get("results", items.get("components", []))

        parts = []
        for item in items[:limit]:
            parts.append(JLCPart(
                lcsc=str(item.get("lcsc", item.get("lcsc_part", ""))),
                mfr=str(item.get("mfr", item.get("mfr_part", ""))),
                package=str(item.get("package", "")),
                description=str(item.get("description", ""))[:200],
                manufacturer=str(item.get("manufacturer", "")),
                stock=int(item.get("stock", 0) or 0),
                price=float(item.get("price", 0) or 0),
            ))

        self._api_cache[query] = parts
        if cache_file and parts:
            cache_file.write_text(json.dumps([
                {"lcsc": p.lcsc, "mfr": p.mfr, "package": p.package,
                 "description": p.description, "manufacturer": p.manufacturer,
                 "stock": p.stock, "price": p.price}
                for p in parts
            ]))

        return parts

    def search(self, query: str, limit: int = 5) -> list[JLCPart]:
        """Search both offline and online sources."""
        results = self.search_basic(query, limit)
        if len(results) < limit and self.use_api:
            api_results = self.search_api(query, limit - len(results))
            seen = {r.lcsc for r in results}
            results.extend(r for r in api_results if r.lcsc not in seen)
        return results[:limit]

    def lookup_lcsc(self, lcsc: str) -> JLCPart | None:
        """Look up a specific LCSC part number."""
        lcsc = lcsc.upper()
        if not lcsc.startswith("C"):
            lcsc = f"C{lcsc}"

        for p in self._load_basic():
            if f"C{p.get('lcsc', '')}" == lcsc:
                return JLCPart(
                    lcsc=lcsc, mfr=p.get("mfr", ""),
                    package=p.get("package", ""),
                    description=p.get("description", ""),
                    stock=int(p.get("stock", 0) or 0),
                    price=_first_price(p.get("price", "")),
                    basic=p.get("basic") == "1",
                    joints=int(p.get("joints", 0) or 0),
                )

        if self.use_api:
            results = self.search_api(lcsc, 1)
            if results:
                return results[0]
        return None


def _first_price(price_str: str) -> float:
    """Extract first tier price from JLC price JSON."""
    try:
        tiers = json.loads(price_str)
        if isinstance(tiers, list) and tiers:
            return float(tiers[0].get("price", 0))
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    try:
        return float(price_str)
    except (ValueError, TypeError):
        return 0.0


def _cache_key(query: str) -> str:
    """Safe filename from query string."""
    import hashlib
    return hashlib.md5(query.encode()).hexdigest()[:12]


def main():
    """Quick CLI search test."""
    import sys
    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "100nF 0603"
    jlc = JLCLookup()

    print(f"Searching: {query!r}")
    print()

    results = jlc.search(query)
    for p in results:
        print(f"  {p.lcsc:10s} {p.package:12s} {p.mfr:30s} ${p.price:.4f}  {p.description[:60]}")

    if not results:
        print("  (no results)")


if __name__ == "__main__":
    main()
