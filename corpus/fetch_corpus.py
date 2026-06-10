"""Idempotent corpus fetcher for the overnight PCB benchmark run.

Usage:
    python3 -m corpus.fetch_corpus [--tier N]

For each source with ``fetch=True``: if ``corpus/sources/{name}/.git`` exists
the clone is skipped, otherwise a shallow clone is performed. Index-only
sources (``fetch=False``) are recorded in the manifest but never cloned.
Failures are recorded and reported; they never abort the run.
"""

import argparse
import subprocess
import sys
from pathlib import Path

CORPUS_DIR = Path(__file__).resolve().parent
SOURCES_DIR = CORPUS_DIR / "sources"

# name, url, tier, difficulty_axis, format, fetch
# All fetch=True URLs verified reachable 2026-06-10 (git ls-remote).
SOURCES = [
    # --- Tier 1: KiCad-native reference recreations of common devboards ---
    {
        "name": "kicad-arduino-boards",
        "url": "https://github.com/sabogalc/KiCad-Arduino-Boards",
        "tier": 1,
        "difficulty_axis": "digital",
        "format": "kicad",
        "fetch": True,
    },
    {
        "name": "stm32-bluepill-kicad",
        # KiCad-native recreation of the classic STM32F103C8T6 Blue Pill.
        # Verified to contain .kicad_sch + .kicad_pcb via GitHub API.
        "url": "https://github.com/alaminwiki/STM32-Blue-Pill-PCB-Design-in-KiCAD",
        "tier": 1,
        "difficulty_axis": "digital",
        "format": "kicad",
        "fetch": True,
    },
    {
        "name": "esp32-c3-devkit",
        # Bare-bones ESP32-C3 devkit, KiCad-native (.kicad_sch + .kicad_pcb verified).
        "url": "https://github.com/Ben-BJD/esp32-c3-bare-bones",
        "tier": 1,
        "difficulty_axis": "digital",
        "format": "kicad",
        "fetch": True,
    },
    # --- Tier 2: fetch + index only ---
    {
        "name": "mutable-eurorack",
        "url": "https://github.com/pichenettes/eurorack",
        "tier": 2,
        "difficulty_axis": "analog_mixed",
        "format": "eagle",
        "fetch": True,
    },
    {
        "name": "coriolis-eurorack",
        "url": "https://github.com/coriolisinstruments/EurorackModules",
        "tier": 2,
        "difficulty_axis": "analog_mixed",
        "format": "kicad",
        "fetch": True,
    },
    # --- Tier 3: fetch + index only ---
    {
        "name": "vesc6",
        "url": "https://github.com/vedderb/bldc-hardware",
        "tier": 3,
        "difficulty_axis": "high_power",
        "format": "kicad",
        "fetch": True,
    },
    # --- Tier 4: index only, too large to clone ---
    {
        "name": "olimex-olinuxino",
        "url": "https://github.com/OLIMEX/OLINUXINO",
        "tier": 4,
        "difficulty_axis": "high_complexity",
        "format": "kicad",
        "fetch": False,
    },
    {
        "name": "antmicro-jetson-orin-baseboard",
        "url": "https://github.com/antmicro/jetson-orin-baseboard",
        "tier": 4,
        "difficulty_axis": "high_complexity",
        "format": "kicad",
        "fetch": False,
    },
]


def source_dir(name):
    return SOURCES_DIR / name


def is_fetched(name):
    """A source counts as fetched when its clone has a .git directory."""
    return (source_dir(name) / ".git").exists()


def fetch_source(src):
    """Fetch one source. Returns a status string; never raises."""
    name = src["name"]
    if not src["fetch"]:
        return "index-only"
    dest = source_dir(name)
    if is_fetched(name):
        return "skipped (already cloned)"
    try:
        proc = subprocess.run(
            ["git", "clone", "--depth", "1", src["url"], str(dest)],
            capture_output=True,
            text=True,
            timeout=600,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return f"FAILED ({exc})"
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()
        return "FAILED ({})".format(tail[-1] if tail else "git clone error")
    return "cloned"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", type=int, default=None, help="Only fetch sources in this tier")
    args = parser.parse_args(argv)

    SOURCES_DIR.mkdir(parents=True, exist_ok=True)

    failures = []
    for src in SOURCES:
        if args.tier is not None and src["tier"] != args.tier:
            continue
        status = fetch_source(src)
        print(f"[tier {src['tier']}] {src['name']:32s} {status:28s} {src['url']}")
        if status.startswith("FAILED"):
            failures.append((src["name"], status))

    if failures:
        print(f"\n{len(failures)} source(s) failed (run is still usable):")
        for name, status in failures:
            print(f"  {name}: {status}")
    else:
        print("\nAll requested sources fetched or indexed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
