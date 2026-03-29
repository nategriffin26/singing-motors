from __future__ import annotations

from pathlib import Path
from typing import Any

from ..artifacts import read_json, read_jsonl, write_json


def import_run_bundle(bundle_dir: str | Path, *, out_path: str | Path | None = None) -> dict[str, Any]:
    root = Path(bundle_dir).expanduser().resolve()
    manifest = read_json(root / "manifest.json")
    replay = {
        "replay_id": f"replay-{root.name}",
        "source_bundle_id": manifest.get("bundle_id", root.name),
        "source_bundle_type": manifest.get("bundle_type", "unknown"),
        "manifest": manifest,
        "analyze": read_json(root / "analyze.json") if (root / "analyze.json").exists() else {},
        "run_metrics": read_json(root / "run_metrics.json") if (root / "run_metrics.json").exists() else {},
        "status_trace": read_jsonl(root / "status_trace.jsonl"),
        "metrics_trace": read_jsonl(root / "metrics_trace.jsonl"),
    }
    if out_path is not None:
        write_json(out_path, replay)
    return replay
