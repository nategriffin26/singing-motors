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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle_dir")
    parser.add_argument("--test-id", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--severity", type=float, default=1.0)
    parser.add_argument("--notes", default="")
    args = parser.parse_args()
    annotations_path = Path(args.bundle_dir).expanduser().resolve() / "annotations.json"
    payload = {"annotations": []}
    if annotations_path.exists():
        payload = json.loads(annotations_path.read_text(encoding="utf-8"))
    payload.setdefault("annotations", []).append(
        {
            "test_id": args.test_id,
            "label": args.label,
            "severity": args.severity,
            "notes": args.notes,
        }
    )
    annotations_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"annotations_path": str(annotations_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
