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

from music2.calibration.runner import CalibrationRunner


def _float_tuple(raw: str) -> tuple[float, ...]:
    return tuple(float(part.strip()) for part in raw.split(",") if part.strip())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("motor_idx", type=int)
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--target-hz", type=float, required=True)
    parser.add_argument("--reversal-gaps-ms", required=True, help="Comma-separated reversal gap ms values")
    parser.add_argument("--duration-s", type=float, default=1.0)
    parser.add_argument("--transport", choices=["hardware", "synthetic"], default="hardware")
    args = parser.parse_args()
    runner = CalibrationRunner.from_config(config_path=args.config)
    bundle_dir = runner.run_reversal_sweep(
        motor_idx=args.motor_idx,
        target_hz=args.target_hz,
        reversal_gaps_ms=_float_tuple(args.reversal_gaps_ms),
        duration_s=args.duration_s,
        transport=args.transport,
    )
    print(json.dumps({"bundle_dir": str(bundle_dir)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
