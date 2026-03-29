from __future__ import annotations

import json
from pathlib import Path

from music2.bench.runner import BenchmarkRunner
from music2.config import load_config
from music2.instrument_profile import load_instrument_profile
from music2.playback_analysis import prepare_playback_artifacts
from music2.sim.compare import compare_plan_to_replay
from music2.sim.program_runner import simulate_playback_program
from music2.sim.replay import import_run_bundle


def test_simulate_playback_program_returns_sections() -> None:
    cfg = load_config("config.toml")
    profile = load_instrument_profile(cfg.instrument_profile_path)
    prepared = prepare_playback_artifacts(cfg=cfg, midi_path="assets/midi/simple4.mid", instrument_profile=profile)
    payload = simulate_playback_program(
        playback_program=prepared.playback_program,
        instrument_profile=profile,
    )
    assert payload["section_count"] == 1
    assert payload["sections"][0]["summary"]["event_group_count"] > 0


def test_compare_plan_to_replay_on_synthetic_bundle(tmp_path: Path) -> None:
    runner = BenchmarkRunner.from_config(cache_root=tmp_path)
    bundle = runner.run_suite(case_id="simple4_smoke", mode="synthetic-run")[0]
    replay = import_run_bundle(bundle.bundle_dir, out_path=tmp_path / "replay.json")

    cfg = load_config("config.toml")
    profile = load_instrument_profile(cfg.instrument_profile_path)
    prepared = prepare_playback_artifacts(cfg=cfg, midi_path="assets/midi/simple4.mid", instrument_profile=profile)
    simulated = simulate_playback_program(
        playback_program=prepared.playback_program,
        instrument_profile=profile,
    )
    simulated_path = tmp_path / "simulated.json"
    simulated_path.write_text(json.dumps(simulated, indent=2), encoding="utf-8")

    comparison = compare_plan_to_replay(
        simulated_path=simulated_path,
        replay_path=tmp_path / "replay.json",
    )
    assert comparison["comparison"]["planned_event_group_count"] > 0
    assert comparison["comparison"]["live_event_groups_started"] > 0
