from __future__ import annotations

from pathlib import Path
from typing import Any

from ..artifacts import ensure_dir, write_json

BENCH_SCHEMA_VERSION = 1
BENCH_RESULT_FILES = (
    "manifest.json",
    "analyze.json",
    "run_metrics.json",
    "status_trace.jsonl",
    "metrics_trace.jsonl",
    "stdout.txt",
    "notes.md",
)


def bench_bundle_dir(base_dir: str | Path, bundle_id: str) -> Path:
    return ensure_dir(Path(base_dir).expanduser().resolve() / bundle_id)


def write_bench_manifest(bundle_dir: str | Path, manifest: dict[str, Any]) -> Path:
    payload = {
        "schema_version": BENCH_SCHEMA_VERSION,
        **manifest,
    }
    return write_json(Path(bundle_dir) / "manifest.json", payload)
