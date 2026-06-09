#!/usr/bin/env python3
"""Enrich manifest descriptions with product page technical details.

Reads manifest JSON files, fetches Adafruit product/learn pages for each board,
extracts technical specs, and writes enriched manifests with an
``enriched_description`` field.

Usage:
    python3 benchmarks/enrich_descriptions.py [--manifest benchmarks/manifest.json]

Requires: requests, beautifulsoup4 (pip install requests beautifulsoup4)
"""

import argparse
import json
import os
import re
import sys
import time

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("Install dependencies: pip install requests beautifulsoup4")


ADAFRUIT_PRODUCT_BASE = "https://www.adafruit.com/product/"
ADAFRUIT_LEARN_SEARCH = "https://learn.adafruit.com/search?q={query}"

TECH_SPEC_KEYS = [
    "processor", "mcu", "flash", "ram", "psram",
    "wifi", "bluetooth", "ble",
    "gpio", "pins", "analog", "pwm", "i2c", "spi", "uart",
    "usb", "usb-c", "micro-usb",
    "display", "tft", "oled", "lcd",
    "battery", "lipo", "charger",
    "sensor", "accelerometer", "gyroscope", "magnetometer",
    "neopixel", "led", "rgb",
    "dimensions", "weight",
    "voltage", "current", "power",
]


def fetch_product_page(slug: str) -> str | None:
    """Try to fetch an Adafruit product page by searching for the slug."""
    try:
        resp = requests.get(
            f"https://www.adafruit.com/?q={slug.replace('-', '+')}",
            timeout=15,
            headers={"User-Agent": "SKiDL-Benchmark/1.0"},
        )
        if resp.status_code == 200:
            return resp.text
    except requests.RequestException:
        pass
    return None


def extract_tech_details(html: str) -> list[str]:
    """Extract technical detail bullet points from product page HTML."""
    soup = BeautifulSoup(html, "html.parser")
    details = []

    for section in soup.find_all(["div", "section"]):
        heading = section.find(["h2", "h3", "h4"])
        if heading and any(kw in heading.get_text().lower() for kw in
                          ["technical details", "features", "specifications", "description"]):
            for li in section.find_all("li"):
                text = li.get_text(strip=True)
                if len(text) > 10 and any(k in text.lower() for k in TECH_SPEC_KEYS):
                    details.append(text)

    return details[:20]


def enrich_board(board: dict) -> dict:
    """Add enriched_description to a board manifest entry."""
    enriched = dict(board)
    slug = board["slug"]

    html = fetch_product_page(slug)
    if html:
        specs = extract_tech_details(html)
        if specs:
            spec_text = " Technical details: " + "; ".join(specs)
            enriched["enriched_description"] = board["description"] + spec_text
            return enriched

    enriched["enriched_description"] = board["description"]
    return enriched


def main():
    parser = argparse.ArgumentParser(description="Enrich benchmark manifests")
    parser.add_argument("--manifest", default="benchmarks/manifest.json")
    parser.add_argument("--output", default=None,
                        help="Output path (default: overwrite input)")
    parser.add_argument("--delay", type=float, default=1.0,
                        help="Delay between requests (seconds)")
    args = parser.parse_args()

    with open(args.manifest) as f:
        boards = json.load(f)

    enriched = []
    for i, board in enumerate(boards):
        print(f"[{i+1}/{len(boards)}] {board['slug']}...", end=" ", flush=True)
        result = enrich_board(board)
        has_extra = result["enriched_description"] != board["description"]
        print("enriched" if has_extra else "no extra data found")
        enriched.append(result)
        if i < len(boards) - 1:
            time.sleep(args.delay)

    output_path = args.output or args.manifest
    with open(output_path, "w") as f:
        json.dump(enriched, f, indent=2)
    print(f"\nWrote {len(enriched)} entries to {output_path}")

    enriched_count = sum(
        1 for b in enriched
        if b.get("enriched_description", "") != b["description"]
    )
    print(f"Enriched: {enriched_count}/{len(enriched)}")


if __name__ == "__main__":
    main()
