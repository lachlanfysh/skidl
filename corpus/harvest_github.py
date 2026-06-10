"""Harvest KiCad projects from GitHub and reverse into CircuitSpec JSON.

Clones repos with KiCad schematics, finds all .kicad_sch root schematics
(ones with a matching .kicad_pro), and reverses each into a spec JSON.

Usage:
    python3 -m corpus.harvest_github                    # clone + reverse all
    python3 -m corpus.harvest_github --reverse-only     # skip cloning
    python3 -m corpus.harvest_github --stats            # just count what we have
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

CORPUS_DIR = Path(__file__).resolve().parent
SOURCES_DIR = CORPUS_DIR / "sources"
SPECS_DIR = CORPUS_DIR / "specs"

# Repos known to contain KiCad-native schematics (.kicad_sch).
# Each entry: (slug, url, difficulty_axis)
# Repos are shallow-cloned; only .kicad_sch/.kicad_pro files matter.
REPOS = [
    # Arduino recreations
    ("kicad-arduino-boards", "https://github.com/sabogalc/KiCad-Arduino-Boards", "digital"),
    ("stm32-bluepill-kicad", "https://github.com/alaminwiki/STM32-Blue-Pill-PCB-Design-in-KiCAD", "digital"),
    ("esp32-c3-devkit", "https://github.com/Ben-BJD/esp32-c3-bare-bones", "digital"),
    # Eurorack / audio
    ("coriolis-eurorack", "https://github.com/coriolisinstruments/EurorackModules", "analog_mixed"),
    # CERN Open Hardware
    ("ohwr-spec", "https://github.com/OHWR/ohwr.org", "high_complexity"),
    ("cern-white-rabbit", "https://github.com/OHWR/wr-starting-kit", "high_complexity"),
    # Antmicro (KiCad-native, high quality)
    ("antmicro-jetson-orin", "https://github.com/antmicro/jetson-orin-baseboard", "high_complexity"),
    ("antmicro-lpddr4-tester", "https://github.com/antmicro/lpddr4-test-board", "high_complexity"),
    ("antmicro-hdmi2usb", "https://github.com/antmicro/hdmi2usb-numato-opsis-hardware", "high_complexity"),
    # OLIMEX (KiCad-native, many boards)
    ("olimex-stm32-h405", "https://github.com/OLIMEX/STM32-H405", "digital"),
    ("olimex-esp32-gateway", "https://github.com/OLIMEX/ESP32-GATEWAY", "digital"),
    ("olimex-esp32-poe", "https://github.com/OLIMEX/ESP32-POE", "digital"),
    ("olimex-stm32-e407", "https://github.com/OLIMEX/STM32-E407", "digital"),
    # Prusa (KiCad-native)
    ("prusa-einsy", "https://github.com/prusa3d/EinsyRambo", "digital"),
    ("prusa-buddy", "https://github.com/prusa3d/Prusa-Firmware-Buddy", "digital"),
    ("prusa-ir-sensor", "https://github.com/prusa3d/MKxS-IR-sensor", "analog_mixed"),
    # Community KiCad projects
    ("polykit-vco8", "https://github.com/polykit/vco-8", "analog_mixed"),
    ("logic-analyzer-pcb", "https://github.com/perehinik/Logic_Analyzer_PCB", "digital"),
    ("ti92-revive", "https://github.com/ccadic/TI92-revive", "digital"),
    ("kiwisdr-pcb", "https://github.com/mfkiwl/KiwiSDR_PCB", "rf"),
    ("picon-one", "https://github.com/fm4dd/picon-one-hw", "digital"),
    # SparkFun (many repos have KiCad versions)
    ("sparkfun-artemis-thing", "https://github.com/sparkfun/SparkFun_Artemis_Thing_Plus", "digital"),
    ("sparkfun-qwiic-mux", "https://github.com/sparkfun/Qwiic_Mux_TCA9548A", "digital"),
    ("sparkfun-openlog", "https://github.com/sparkfun/OpenLog", "digital"),
    # RP2040 designs
    ("rp2040-minimal", "https://github.com/solarkennedy/rp2040-minimal-kicad", "digital"),
    # Keyboard community (lots of KiCad)
    ("aesir-keyboards", "https://github.com/modern-hobbyist/aesir", "digital"),
]


def clone_repo(slug: str, url: str) -> tuple[str, str]:
    dest = SOURCES_DIR / slug
    if (dest / ".git").exists():
        return slug, "exists"
    try:
        proc = subprocess.run(
            ["git", "clone", "--depth", "1", url, str(dest)],
            capture_output=True, text=True, timeout=120,
        )
        if proc.returncode != 0:
            return slug, f"FAILED: {proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else 'unknown'}"
        return slug, "cloned"
    except (subprocess.TimeoutExpired, OSError) as exc:
        return slug, f"FAILED: {exc}"


def find_kicad_projects(source_dir: Path) -> list[tuple[str, Path]]:
    """Find all root KiCad schematics (those with matching .kicad_pro)."""
    projects = []
    for sch in sorted(source_dir.rglob("*.kicad_sch")):
        # Skip sub-schematics (hierarchical sheets)
        pro = sch.with_suffix(".kicad_pro")
        if not pro.exists():
            continue
        # Skip backup/test directories
        parts = sch.parts
        if any(p.startswith('.') or p in ('backup', 'backups', 'test', 'tests', '_old') for p in parts):
            continue
        slug = re.sub(r"[^a-z0-9]+", "-", sch.stem.lower()).strip("-")
        projects.append((slug, sch.parent))
    return projects


def reverse_project(slug: str, proj_path: Path) -> tuple[str, bool, str]:
    """Reverse a KiCad project into a CircuitSpec JSON. Returns (slug, ok, message)."""
    from corpus.kicad_to_spec import reverse_schematic, validate_spec
    from corpus.reference_oracle import OracleError

    try:
        spec = reverse_schematic(proj_path)
    except OracleError as e:
        return slug, False, f"oracle: {e}"
    except Exception as e:
        return slug, False, f"{type(e).__name__}: {e}"

    valid, vmsg = validate_spec(spec)
    if not valid:
        return slug, False, f"invalid: {vmsg[:80]}"

    n_parts = len(spec["parts"])
    n_nets = len(spec["nets"])
    if n_parts == 0:
        return slug, False, "0 parts"

    out_path = SPECS_DIR / f"ref-{slug}.json"
    out_path.write_text(json.dumps(spec, indent=2) + "\n")
    return slug, True, f"{n_parts}P {n_nets}N"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reverse-only", action="store_true",
                        help="Skip cloning, reverse existing sources only")
    parser.add_argument("--stats", action="store_true",
                        help="Just report counts")
    args = parser.parse_args(argv)

    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    SPECS_DIR.mkdir(parents=True, exist_ok=True)

    if args.stats:
        specs = list(SPECS_DIR.glob("ref-*.json"))
        sources = [d for d in SOURCES_DIR.iterdir() if d.is_dir() and (d / ".git").exists()]
        projects = []
        for src in sources:
            projects.extend(find_kicad_projects(src))
        print(f"Sources: {len(sources)} repos cloned")
        print(f"KiCad projects found: {len(projects)}")
        print(f"Specs generated: {len(specs)}")
        return 0

    # Phase 1: Clone
    if not args.reverse_only:
        print(f"=== Cloning {len(REPOS)} repos ===")
        for slug, url, _ in REPOS:
            slug_result, status = clone_repo(slug, url)
            print(f"  {slug_result:40s} {status}")

    # Phase 2: Discover all KiCad projects across all sources
    print(f"\n=== Discovering KiCad projects ===")
    all_projects = []
    for source_dir in sorted(SOURCES_DIR.iterdir()):
        if not source_dir.is_dir() or not (source_dir / ".git").exists():
            continue
        projects = find_kicad_projects(source_dir)
        if projects:
            print(f"  {source_dir.name:40s} {len(projects)} project(s)")
            all_projects.extend(projects)

    print(f"\nTotal: {len(all_projects)} KiCad projects found")

    # Phase 3: Reverse each into a spec
    print(f"\n=== Reversing into CircuitSpec JSON ===")
    ok_count = 0
    fail_count = 0
    for slug, proj_path in all_projects:
        spec_path = SPECS_DIR / f"ref-{slug}.json"
        if spec_path.exists():
            ok_count += 1
            continue
        result_slug, ok, msg = reverse_project(slug, proj_path)
        marker = "OK" if ok else "FAIL"
        print(f"  {marker:4s} ref-{result_slug:40s} {msg}")
        if ok:
            ok_count += 1
        else:
            fail_count += 1

    print(f"\n=== Summary ===")
    print(f"Reversed: {ok_count} ok, {fail_count} failed")
    print(f"Total specs: {len(list(SPECS_DIR.glob('ref-*.json')))} + {len(list(SPECS_DIR.glob('[!r]*.json')))} non-ref")
    return 0


if __name__ == "__main__":
    sys.exit(main())
