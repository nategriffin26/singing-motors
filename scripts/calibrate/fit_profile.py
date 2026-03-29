#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from music2.calibration.fit import fit_profile_from_bundles, write_profile_patch


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instrument-profile", required=True)
    parser.add_argument("--bundle", action="append", required=True, help="Calibration bundle directory (repeatable)")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    patch = fit_profile_from_bundles(
        instrument_path=args.instrument_profile,
        bundle_dirs=tuple(args.bundle),
    )
    write_profile_patch(args.out, patch)
    print(json.dumps({"patch_path": str(Path(args.out).resolve())}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
