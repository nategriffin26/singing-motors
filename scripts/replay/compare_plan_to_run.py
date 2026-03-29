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

from music2.sim.compare import compare_plan_to_replay


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("simulated")
    parser.add_argument("--replay", required=True)
    args = parser.parse_args()
    print(json.dumps(compare_plan_to_replay(simulated_path=args.simulated, replay_path=args.replay), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
