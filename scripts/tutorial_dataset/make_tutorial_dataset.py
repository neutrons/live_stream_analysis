#!/usr/bin/env python3
"""Write the tutorial dataset to tests/data/tutorial/ at the sizes the trim study chose.

The pulse counts come from results/summary.json, so re-running trim_study.py and then this script
keeps the two in step. A manifest records each file's source run, what was kept, and its SHA-256.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from trim import trim_adara, trim_nexus  # noqa: E402

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
RUN_DIR = REPO.parent / "nomad-adara-carbon-share" / "20260619-140639.403016382-run-244114"

# Study input name -> (source files, tutorial file name, role)
DATASET = {
    "nexus_diamond": ([REPO / "nexus_files/diamond/NOM_244114.nxs.h5"], "NOM_244114.nxs.h5", "Sample"),
    "adara_diamond": (
        [RUN_DIR / "m00000001-f00000001-run-244114.adara", RUN_DIR / "m00000001-f00000016-run-244114.adara"],
        "m00000001-f00000001-run-244114.adara",
        "Sample",
    ),
    "nexus_background": ([REPO / "nexus_files/mt_pac_can/NOM_243710.nxs.h5"], "NOM_243710.nxs.h5", "Background"),
    "nexus_normalization": ([REPO / "nexus_files/vanadium/NOM_243712.nxs.h5"], "NOM_243712.nxs.h5", "Normalization"),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--summary", type=Path, default=HERE / "results" / "summary.json")
    parser.add_argument("--output-dir", type=Path, default=REPO / "tests" / "data" / "tutorial")
    args = parser.parse_args()

    plan = json.loads(args.summary.read_text(encoding="utf-8"))["plan"]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for kind, (sources, name, role) in DATASET.items():
        pulses = plan[kind]["pulses"]
        destination = args.output_dir / name
        if kind == "adara_diamond":
            kept = trim_adara(sources[0], sources[1], destination, pulses)
        else:
            kept = trim_nexus(sources[0], destination, pulses)
        manifest[name] = {
            "role": role,
            "source": [str(source.relative_to(REPO.parent)) for source in sources],
            **kept,
            "bytes": destination.stat().st_size,
            "sha256": sha256(destination),
        }
        print(f"{destination.relative_to(REPO)}: {destination.stat().st_size / 1e6:.2f} MB, {kept}")
    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"{manifest_path.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
