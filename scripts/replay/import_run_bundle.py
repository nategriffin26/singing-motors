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

from music2.sim.replay import import_run_bundle


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle_dir")
    args = parser.parse_args()
    print(json.dumps(import_run_bundle(args.bundle_dir), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
