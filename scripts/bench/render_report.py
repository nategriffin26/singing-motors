#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from music2.bench.compare import compare_benchmark_bundles, render_benchmark_markdown_report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("left_bundle")
    parser.add_argument("right_bundle")
    args = parser.parse_args()
    comparison = compare_benchmark_bundles(args.left_bundle, args.right_bundle)
    sys.stdout.write(render_benchmark_markdown_report(comparison))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
