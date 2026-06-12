"""Pre-warm the converted_parts DB cache with popular LCSC ICs.

Usage:
    python3 -m corpus.jlc.prewarm --limit 500 --database-url $DATABASE_URL

Reads the IC CSV, picks the top N parts by stock from each subcategory,
and runs easyeda2kicad conversion for each. Results are cached on disk
and (if DATABASE_URL is set) persisted to Postgres for cross-deploy access.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

CORPUS_DIR = Path(__file__).resolve().parent
ICS_CSV = CORPUS_DIR / "jlcpcb-ics.csv"
CACHE_DIR = CORPUS_DIR / "easyeda_cache"


def _top_parts_by_category(limit: int = 500) -> list[dict]:
    """Select top parts per subcategory by stock level."""
    by_cat: dict[str, list[dict]] = defaultdict(list)

    with open(ICS_CSV) as f:
        for row in csv.DictReader(f):
            try:
                stock = int(row.get("stock", 0))
            except (ValueError, TypeError):
                stock = 0
            if stock < 100:
                continue
            by_cat[row.get("subcategory", "other")].append({
                "lcsc": row["lcsc"],
                "mfr": row.get("mfr", ""),
                "stock": stock,
            })

    for parts in by_cat.values():
        parts.sort(key=lambda p: p["stock"], reverse=True)

    n_cats = len(by_cat)
    per_cat = max(3, limit // max(n_cats, 1))
    selected = []
    seen = set()
    for cat in sorted(by_cat.keys()):
        for p in by_cat[cat][:per_cat]:
            if p["lcsc"] not in seen:
                selected.append(p)
                seen.add(p["lcsc"])
            if len(selected) >= limit:
                break
        if len(selected) >= limit:
            break

    if len(selected) < limit:
        all_parts = []
        for parts in by_cat.values():
            for p in parts:
                if p["lcsc"] not in seen:
                    all_parts.append(p)
        all_parts.sort(key=lambda p: p["stock"], reverse=True)
        for p in all_parts:
            if len(selected) >= limit:
                break
            selected.append(p)
            seen.add(p["lcsc"])

    return selected


def _convert_one(lcsc: str) -> dict | None:
    """Convert a single LCSC part via easyeda2kicad. Returns metadata or None."""
    cache_path = CACHE_DIR / lcsc
    if cache_path.is_dir() and any(cache_path.iterdir()):
        return {"lcsc": lcsc, "cached": True}

    try:
        from easyeda2kicad.easyeda.easyeda_api import EasyedaApi
        from easyeda2kicad.easyeda.easyeda_importer import EeSymbol, Easyeda3dModelImporter, EeFootprint
        from easyeda2kicad.kicad.export_kicad_footprint import ExporterFootprintKicad
        from easyeda2kicad.kicad.export_kicad_symbol import ExporterSymbolKicad
        from easyeda2kicad.kicad.export_kicad_3d_model import Exporter3dModelKicad
    except ImportError:
        return None

    api = EasyedaApi()
    try:
        info = api.get_cad_data_of_component(lcsc_id=lcsc)
    except Exception:
        return None

    if info is None:
        return None

    cache_path.mkdir(parents=True, exist_ok=True)
    meta = {"lcsc": lcsc, "files": []}

    try:
        sym = EeSymbol(info)
        exporter = ExporterSymbolKicad(sym)
        sym_path = str(cache_path / f"{lcsc}.kicad_sym")
        exporter.export(sym_path)
        meta["symbol"] = f"{lcsc}.kicad_sym"
        meta["files"].append(sym_path)
    except Exception:
        pass

    try:
        fp = EeFootprint(info)
        exporter = ExporterFootprintKicad(fp)
        fp_dir = cache_path / f"{lcsc}.pretty"
        fp_dir.mkdir(exist_ok=True)
        fp_path = str(fp_dir / f"{lcsc}.kicad_mod")
        exporter.export(fp_path)
        meta["footprint"] = f"{lcsc}.pretty/{lcsc}.kicad_mod"
        meta["files"].append(fp_path)
    except Exception:
        pass

    try:
        model = Easyeda3dModelImporter(info)
        exporter = Exporter3dModelKicad(model)
        shapes_dir = cache_path / f"{lcsc}.3dshapes"
        shapes_dir.mkdir(exist_ok=True)
        exporter.export(str(shapes_dir / lcsc))
        for f in shapes_dir.iterdir():
            meta["files"].append(str(f))
    except Exception:
        pass

    if not meta["files"]:
        return None

    meta["cached"] = False
    return meta


async def _persist_to_db(database_url: str, results: list[dict]) -> int:
    """Persist converted parts to Postgres. Returns count stored."""
    import asyncpg

    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5)
    stored = 0

    for meta in results:
        lcsc = meta["lcsc"]
        cache_path = CACHE_DIR / lcsc

        sym_data = None
        sym_file = cache_path / f"{lcsc}.kicad_sym"
        if sym_file.exists():
            sym_data = sym_file.read_bytes()

        fp_data = None
        fp_file = cache_path / f"{lcsc}.pretty" / f"{lcsc}.kicad_mod"
        if fp_file.exists():
            fp_data = fp_file.read_bytes()

        step_data = None
        shapes_dir = cache_path / f"{lcsc}.3dshapes"
        if shapes_dir.is_dir():
            for f in shapes_dir.iterdir():
                if f.suffix == ".step":
                    step_data = f.read_bytes()
                    break

        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO converted_parts (lcsc, sym_data, fp_data, step_data, meta)
                       VALUES ($1, $2, $3, $4, $5)
                       ON CONFLICT (lcsc) DO UPDATE
                       SET sym_data = $2, fp_data = $3, step_data = $4, meta = $5""",
                    lcsc, sym_data, fp_data, step_data, json.dumps(meta),
                )
            stored += 1
        except Exception as exc:
            print(f"  DB error for {lcsc}: {exc}", file=sys.stderr)

    await pool.close()
    return stored


def main():
    parser = argparse.ArgumentParser(description="Pre-warm LCSC parts cache")
    parser.add_argument("--limit", type=int, default=500, help="Max parts to convert")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"), help="Postgres URL")
    parser.add_argument("--dry-run", action="store_true", help="List parts without converting")
    args = parser.parse_args()

    print(f"Selecting top {args.limit} parts from {ICS_CSV.name}...")
    parts = _top_parts_by_category(args.limit)
    print(f"Selected {len(parts)} parts across categories")

    if args.dry_run:
        for p in parts[:20]:
            print(f"  {p['lcsc']}: {p['mfr']} (stock: {p['stock']})")
        if len(parts) > 20:
            print(f"  ... and {len(parts) - 20} more")
        return

    CACHE_DIR.mkdir(exist_ok=True)
    converted = 0
    cached = 0
    failed = 0
    results = []
    t0 = time.time()

    for i, part in enumerate(parts):
        lcsc = part["lcsc"]
        try:
            meta = _convert_one(lcsc)
            if meta is None:
                failed += 1
                print(f"  [{i+1}/{len(parts)}] {lcsc} ({part['mfr']}): FAILED")
            elif meta.get("cached"):
                cached += 1
                results.append(meta)
            else:
                converted += 1
                results.append(meta)
                print(f"  [{i+1}/{len(parts)}] {lcsc} ({part['mfr']}): OK")
        except Exception as exc:
            failed += 1
            print(f"  [{i+1}/{len(parts)}] {lcsc}: ERROR {exc}")

        if (i + 1) % 50 == 0:
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed
            eta = (len(parts) - i - 1) / rate
            print(f"  Progress: {i+1}/{len(parts)} ({converted} new, {cached} cached, {failed} failed) ETA: {eta:.0f}s")

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.0f}s: {converted} converted, {cached} cached, {failed} failed")

    if args.database_url and results:
        print(f"Persisting {len(results)} parts to Postgres...")
        stored = asyncio.run(_persist_to_db(args.database_url, results))
        print(f"Stored {stored} parts in converted_parts table")
    elif not args.database_url:
        print("No DATABASE_URL — disk cache only (set --database-url to persist)")


if __name__ == "__main__":
    main()
