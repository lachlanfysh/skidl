"""Build corpus/manifest.jsonl for the overnight PCB benchmark run.

Usage:
    python3 -m corpus.build_manifest

Inputs:
  1. benchmarks/manifest.json        (20 Adafruit boards, names need slug mapping)
  2. benchmarks/manifest_batch3.json (30 Adafruit boards, slug is canonical)
  3. Cloned Tier-1 reference repos in corpus/sources/ (KiCad-native projects)
  4. Tier 2-4 sources from corpus.fetch_corpus.SOURCES (indexed only)

Output: one JSON object per line in corpus/manifest.jsonl.
"""

import difflib
import json
import re
import sys
from pathlib import Path

from corpus.fetch_corpus import SOURCES, is_fetched, source_dir

CORPUS_DIR = Path(__file__).resolve().parent
REPO_ROOT = CORPUS_DIR.parent
BENCHMARKS_DIR = REPO_ROOT / "benchmarks"
RESULTS_DIR = BENCHMARKS_DIR / "results"
MANIFEST_OUT = CORPUS_DIR / "manifest.jsonl"

README_CHARS = 1500

# Hand-checked mapping from benchmarks/manifest.json "name" -> results dir slug.
# Fuzzy matching (difflib) is used as a fallback for anything not listed here.
HAND_MAP = {
    "NeoPixel Ring (12, 16, 24)": "neopixel-ring",
    "BNO055 9-DOF Absolute Orientation Sensor Breakout": "bno055",
    "INA219 High Side DC Current Sensor Breakout": "ina219",
    "BME280 Temperature/Humidity/Pressure Sensor Breakout": "bme280",
    "MCP9808 High Accuracy I2C Temperature Sensor Breakout": "mcp9808",
    "Feather M0 Basic Proto (SAMD21)": "feather-m0-basic-proto",
    "Motor Shield V2 for Arduino": "motor-shield-v2",
    "VS1053 Codec (MP3/WAV/MIDI/Ogg) Breakout": "vs1053",
    "Si5351A Clock Generator Breakout": "si5351a",
    "MAX98357 I2S Class-D Mono Amp Breakout": "max98357-i2s-amp",
    "Feather RP2040": "feather-rp2040",
    "Circuit Playground Express": "circuit-playground-express",
    "MacroPad RP2040": "macropad-rp2040",
    "Feather ESP32-S3": "feather-esp32-s3",
    "HUZZAH32 ESP32 Feather": "huzzah32-esp32-feather",
    "Grand Central M4 Express (SAMD51)": "grand-central",
    "PyPortal - CircuitPython Powered Internet Display": "pyportal",
    "Feather nRF52840 Sense (Bluefruit)": "feather-nrf52840-sense",
    "Metro M4 Express (SAMD51)": "metro-m4-express",
    "CLUE nRF52840 Express": "clue-nrf52840",
}

# Adafruit boards whose difficulty axis is analog/mixed-signal
# (audio, clock, analog sensor front ends, power/motor stages).
ANALOG_MIXED_SLUGS = {
    # batch 1
    "ina219",            # precision current-sense amplifier front end
    "vs1053",            # audio codec with analog out
    "si5351a",           # RF clock generator
    "max98357-i2s-amp",  # class-D audio amplifier
    "motor-shield-v2",   # motor power stage, flyback diodes
    # batch 3
    "ads1115-adc",            # precision ADC with PGA
    "als-pt19-light-sensor",  # analog light sensor
    "max4466-mic-amplifier",  # electret mic analog amplifier
    "pam8302-mono-amplifier", # audio amplifier
    "max31865-rtd-amplifier", # precision RTD analog front end
    "max31856-thermocouple",  # precision thermocouple front end
    "ds3231-rtc",             # TCXO precision clock
}


def slugify(text):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")


def map_name_to_slug(name, slugs):
    """Map a benchmarks/manifest.json board name to a results dir slug."""
    if name in HAND_MAP:
        return HAND_MAP[name]
    matches = difflib.get_close_matches(slugify(name), slugs, n=1, cutoff=0.4)
    if matches:
        return matches[0]
    raise ValueError(f"could not map board name to results dir slug: {name!r}")


def nearest_readme(start_dir, stop_dir):
    """First README found walking up from start_dir to stop_dir (inclusive)."""
    d = Path(start_dir)
    stop = Path(stop_dir)
    while True:
        for pattern in ("README.md", "README.rst", "README.txt", "README*"):
            hits = sorted(p for p in d.glob(pattern) if p.is_file())
            if hits:
                return hits[0]
        if d == stop:
            return None
        d = d.parent


def readme_excerpt(start_dir, stop_dir):
    readme = nearest_readme(start_dir, stop_dir)
    if readme is None:
        return ""
    try:
        return readme.read_text(errors="replace")[:README_CHARS].strip()
    except OSError:
        return ""


def adafruit_rows():
    """Rows 1+2: 50 Adafruit boards from the two benchmark manifests."""
    slugs = sorted(p.name for p in RESULTS_DIR.iterdir() if p.is_dir())
    rows = []

    batch1 = json.loads((BENCHMARKS_DIR / "manifest.json").read_text())
    for board in batch1:
        slug = map_name_to_slug(board["name"], slugs)
        if not (RESULTS_DIR / slug).is_dir():
            raise ValueError(f"mapped slug {slug!r} is not a results dir ({board['name']!r})")
        rows.append(make_adafruit_row(slug, board["tier"], board["description"]))

    batch3 = json.loads((BENCHMARKS_DIR / "manifest_batch3.json").read_text())
    for board in batch3:
        slug = board["slug"]
        if not (RESULTS_DIR / slug).is_dir():
            raise ValueError(f"batch3 slug {slug!r} is not a results dir")
        rows.append(make_adafruit_row(slug, board["tier"], board["description"]))

    return rows


def make_adafruit_row(slug, tier, description):
    axis = "analog_mixed" if slug in ANALOG_MIXED_SLUGS else "digital"
    return {
        "board_id": slug,
        "tier": tier,
        "source": "adafruit",
        "difficulty_axis": axis,
        "nl_source": "marketing",
        "description": description,
        "validation_mode": "internal",
        "spec_path": f"corpus/specs/{slug}.json",
    }


def find_kicad_projects(repo_dir):
    """KiCad projects = dirs containing a .kicad_pro and at least one .kicad_sch.

    Falls back to bare .kicad_sch directories when no .kicad_pro exists.
    Returns sorted [(project_name, project_dir)].
    """
    projects = {}
    for pro in sorted(repo_dir.rglob("*.kicad_pro")):
        if any(pro.parent.glob("*.kicad_sch")):
            projects[pro.parent] = pro.stem
    for sch in sorted(repo_dir.rglob("*.kicad_sch")):
        if sch.parent not in projects:
            projects[sch.parent] = sch.stem
    return sorted((name, d) for d, name in projects.items())


def reference_rows():
    """Row per KiCad project in each cloned Tier-1 reference repo."""
    rows = []
    for src in SOURCES:
        if src["tier"] != 1 or not src["fetch"] or not is_fetched(src["name"]):
            continue
        repo_dir = source_dir(src["name"])
        for proj_name, proj_dir in find_kicad_projects(repo_dir):
            rows.append({
                "board_id": f"ref-{src['name']}-{slugify(proj_name)}",
                "tier": 1,
                "source": src["name"],
                "difficulty_axis": src["difficulty_axis"],
                "nl_source": "readme",
                "description": readme_excerpt(proj_dir, repo_dir),
                "reference_project_path": str(proj_dir.relative_to(REPO_ROOT)),
                "validation_mode": "reference",
            })
    return rows


def indexed_rows():
    """One row per Tier 2-4 source repo (fetched or index-only)."""
    rows = []
    for src in SOURCES:
        if src["tier"] == 1:
            continue
        fetched = src["fetch"] and is_fetched(src["name"])
        repo_dir = source_dir(src["name"])
        rows.append({
            "board_id": src["name"],
            "tier": src["tier"],
            "source": src["name"],
            "url": src["url"],
            "format": src["format"],
            "difficulty_axis": src["difficulty_axis"],
            "nl_source": "readme",
            "description": readme_excerpt(repo_dir, repo_dir) if fetched else "",
            "validation_mode": "indexed_only",
            "fetched": fetched,
        })
    return rows


def build_manifest():
    rows = adafruit_rows() + reference_rows() + indexed_rows()

    deduped, seen = [], set()
    for row in rows:
        if row["board_id"] in seen:
            print(f"WARNING: duplicate board_id {row['board_id']!r} dropped", file=sys.stderr)
            continue
        seen.add(row["board_id"])
        deduped.append(row)
    return deduped


def main():
    rows = build_manifest()
    with open(MANIFEST_OUT, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")

    print(f"{'board_id':44s} {'tier':>4s} {'axis':14s} {'mode':14s} source")
    print("-" * 100)
    for row in rows:
        print(f"{row['board_id']:44s} {row['tier']:>4d} {row['difficulty_axis']:14s} "
              f"{row['validation_mode']:14s} {row['source']}")

    counts = {}
    for row in rows:
        counts[row["validation_mode"]] = counts.get(row["validation_mode"], 0) + 1
    print("-" * 100)
    print(f"wrote {len(rows)} rows to {MANIFEST_OUT.relative_to(REPO_ROOT)}")
    for mode, n in sorted(counts.items()):
        print(f"  {mode}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
