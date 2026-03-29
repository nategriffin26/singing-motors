#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def _ensure_music2_python() -> None:
    if os.environ.get("MUSIC2_SCRIPT_BOOTSTRAPPED") == "1":
        return
    try:
        import mido  # noqa: F401
        return
    except ImportError:
        pass

    music2_bin = shutil.which("music2")
    if not music2_bin:
        return
    first_line = Path(music2_bin).read_text(encoding="utf-8").splitlines()[0].strip()
    if not first_line.startswith("#!"):
        return
    target_python = first_line[2:]
    if not target_python or Path(target_python).resolve() == Path(sys.executable).resolve():
        return

    env = dict(os.environ)
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(SRC_ROOT) if not existing_pythonpath else f"{SRC_ROOT}:{existing_pythonpath}"
    env["MUSIC2_SCRIPT_BOOTSTRAPPED"] = "1"
    os.execve(target_python, [target_python, str(Path(__file__).resolve()), *sys.argv[1:]], env)


_ensure_music2_python()

from music2.bench.runner import BenchmarkRunner


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_id")
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--mode", choices=["analyze-only", "hardware-run", "synthetic-run"], default="analyze-only")
    args = parser.parse_args()
    runner = BenchmarkRunner.from_config(config_path=args.config)
    case = runner.corpus.get_case(args.case_id)
    bundle = runner.run_case(case, mode=args.mode)
    print(json.dumps({"bundle_dir": str(bundle.bundle_dir)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
