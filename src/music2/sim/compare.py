from __future__ import annotations

from pathlib import Path
from typing import Any

from ..artifacts import read_json, write_json


def compare_plan_to_replay(
    *,
    simulated_path: str | Path,
    replay_path: str | Path,
    out_path: str | Path | None = None,
) -> dict[str, Any]:
    simulated = read_json(simulated_path)
    replay = read_json(replay_path)
    simulated_sections = simulated.get("sections", [])
    first_section = simulated_sections[0] if simulated_sections else {}
    simulated_summary = first_section.get("summary", {})
    analyze_plan = replay.get("analyze", {}).get("playback_plan", {})
    metrics = replay.get("run_metrics", {}).get("metrics", {})

    divergence: list[str] = []
    if int(metrics.get("underrun_count", 0)) > 0:
        divergence.append("transport_starvation")
    if int(metrics.get("scheduler_guard_hits", 0)) > 0 or int(metrics.get("scheduling_late_max_us", 0)) > 0:
        divergence.append("scheduler_lateness")
    if int(metrics.get("control_overrun_count", 0)) > 0:
        divergence.append("backend_overrun")
    if int(metrics.get("launch_guard_count", 0)) > 0:
        divergence.append("calibration_mismatch")
    if int(first_section.get("summary", {}).get("risk_hit_count", 0)) > 0:
        divergence.append("profile_out_of_range_operation")

    comparison = {
        "simulated_event_group_count": simulated_summary.get("event_group_count", 0),
        "planned_event_group_count": analyze_plan.get("event_group_count", 0),
        "live_event_groups_started": metrics.get("event_groups_started", 0),
        "simulated_duration_total_us": simulated_summary.get("duration_total_us", 0),
        "planned_duration_total_us": analyze_plan.get("duration_total_us", 0),
        "risk_hit_count": simulated_summary.get("risk_hit_count", 0),
        "divergence_classes": divergence,
    }
    payload = {
        "comparison": comparison,
        "summary_markdown": render_plan_vs_replay_markdown(comparison),
    }
    if out_path is not None:
        write_json(out_path, payload)
    return payload


def render_plan_vs_replay_markdown(comparison: dict[str, Any]) -> str:
    lines = [
        "# Plan vs Replay",
        "",
        f"- Simulated event groups: `{comparison['simulated_event_group_count']}`",
        f"- Planned event groups: `{comparison['planned_event_group_count']}`",
        f"- Live event groups started: `{comparison['live_event_groups_started']}`",
        f"- Simulated duration: `{comparison['simulated_duration_total_us']}` us",
        f"- Planned duration: `{comparison['planned_duration_total_us']}` us",
        f"- Simulated risk hits: `{comparison['risk_hit_count']}`",
        f"- Divergence classes: {', '.join(comparison['divergence_classes']) or 'none'}",
        "",
    ]
    return "\n".join(lines)
