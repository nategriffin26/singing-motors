#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
from typing import Any


_METRIC_KEYS = [
    "underrun_count",
    "pulse_edge_drop_count",
    "pulse_late_max_us",
    "pulse_timebase_rebase_count",
    "pulse_timebase_rebase_lost_us",
    "pulse_target_update_count",
    "pulse_ramp_change_count",
    "pulse_stop_after_ramp_count",
    "scheduling_late_max_us",
    "timer_empty_events",
    "timer_restart_count",
    "queue_high_water",
    "playback_slew_clamp_count",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _flatten_rows(path: Path, payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    # Format from scripts/rca/hw_snapshot.py
    for row in payload.get("results", []):
        run = row.get("run", {})
        metrics = run.get("metrics", {})
        if metrics:
            rows.append(
                {
                    "source": str(path),
                    "run_id": row.get("run_id"),
                    "profile": payload.get("profile"),
                    "transpose": payload.get("transpose"),
                    "midi_path": payload.get("midi_path"),
                    "metrics": metrics,
                }
            )

    # Fallback format from one-off snapshots
    if not rows and "run" in payload and isinstance(payload.get("run"), dict):
        run = payload["run"]
        metrics = run.get("metrics", {})
        if metrics:
            rows.append(
                {
                    "source": str(path),
                    "run_id": 1,
                    "profile": payload.get("profile"),
                    "transpose": payload.get("transpose") or payload.get("analysis", {}).get("transpose"),
                    "midi_path": payload.get("midi") or payload.get("midi_path"),
                    "metrics": metrics,
                }
            )

    return rows


def _summary_for_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"run_count": 0, "metrics": {}}

    metrics_summary: dict[str, Any] = {}
    for key in _METRIC_KEYS:
        values = [int(row["metrics"].get(key, 0)) for row in rows]
        metrics_summary[key] = {
            "min": min(values),
            "max": max(values),
            "mean": round(statistics.fmean(values), 3),
            "median": int(round(statistics.median(values))),
            "values": values,
        }

    return {
        "run_count": len(rows),
        "metrics": metrics_summary,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate playback metrics across RCA JSON files")
    parser.add_argument("inputs", nargs="+", help="Input JSON files")
    parser.add_argument("--out", required=True, help="Output JSON path")
    args = parser.parse_args()

    rows_all: list[dict[str, Any]] = []
    by_file: dict[str, dict[str, Any]] = {}

    for raw in args.inputs:
        path = Path(raw).expanduser().resolve()
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = _flatten_rows(path, payload)
        rows_all.extend(rows)
        by_file[str(path)] = {
            "run_count": len(rows),
            "summary": _summary_for_rows(rows),
        }

    aggregate = _summary_for_rows(rows_all)
    output = {
        "generated_at": _now_iso(),
        "input_count": len(args.inputs),
        "files_seen": sorted(by_file.keys()),
        "by_file": by_file,
        "aggregate": aggregate,
    }

    out_path = Path(args.out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
