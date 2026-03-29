from __future__ import annotations

import json
from pathlib import Path

from music2.bench.compare import compare_benchmark_bundles
from music2.bench.corpus import load_benchmark_corpus
from music2.bench.runner import BenchmarkRunner
from music2.sim.replay import import_run_bundle


def test_benchmark_corpus_loads_cases() -> None:
    corpus = load_benchmark_corpus()
    assert corpus.corpus_version == 1
    assert any(case.case_id == "simple4_smoke" for case in corpus.cases)


def test_benchmark_runner_writes_analyze_only_bundle(tmp_path: Path) -> None:
    runner = BenchmarkRunner.from_config(cache_root=tmp_path)
    bundle = runner.run_suite(case_id="simple4_smoke", mode="analyze-only")[0]
    assert (bundle.bundle_dir / "manifest.json").exists()
    analyze = json.loads((bundle.bundle_dir / "analyze.json").read_text(encoding="utf-8"))
    assert analyze["case_id"] == "simple4_smoke"
    assert analyze["playback_plan"]["event_group_count"] > 0


def test_benchmark_runner_synthetic_run_can_feed_replay(tmp_path: Path) -> None:
    runner = BenchmarkRunner.from_config(cache_root=tmp_path)
    bundle = runner.run_suite(case_id="simple4_smoke", mode="synthetic-run")[0]
    replay = import_run_bundle(bundle.bundle_dir)
    assert replay["source_bundle_type"] == "benchmark"
    assert len(replay["status_trace"]) > 0
    assert len(replay["metrics_trace"]) > 0


def test_compare_benchmark_bundles_reports_zero_delta_for_same_case(tmp_path: Path) -> None:
    runner = BenchmarkRunner.from_config(cache_root=tmp_path)
    left = runner.run_suite(case_id="simple4_smoke", mode="synthetic-run")[0]
    right = runner.run_suite(case_id="simple4_smoke", mode="synthetic-run")[0]
    comparison = compare_benchmark_bundles(left.bundle_dir, right.bundle_dir)
    assert comparison["deltas"]["event_group_count_delta"] == 0
    assert comparison["likely_regression_layer"] == "inconclusive"
