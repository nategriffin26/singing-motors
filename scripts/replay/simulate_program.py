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

from music2.config import load_config
from music2.instrument_profile import load_instrument_profile
from music2.playback_analysis import prepare_playback_artifacts
from music2.sim.program_runner import simulate_playback_program


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("midi_path")
    parser.add_argument("--config", default="config.toml")
    args = parser.parse_args()
    cfg = load_config(args.config)
    instrument_profile = load_instrument_profile(cfg.instrument_profile_path)
    prepared = prepare_playback_artifacts(cfg=cfg, midi_path=args.midi_path, instrument_profile=instrument_profile)
    print(json.dumps(simulate_playback_program(playback_program=prepared.playback_program, instrument_profile=instrument_profile), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
