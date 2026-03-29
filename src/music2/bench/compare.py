from __future__ import annotations

from pathlib import Path
from typing import Any

from ..artifacts import read_json


def _load_bundle(bundle_dir: str | Path) -> dict[str, Any]:
    root = Path(bundle_dir).expanduser().resolve()
    return {
        "manifest": read_json(root / "manifest.json"),
        "analyze": read_json(root / "analyze.json"),
        "run_metrics": read_json(root / "run_metrics.json"),
    }


def compare_benchmark_bundles(left_bundle: str | Path, right_bundle: str | Path) -> dict[str, Any]:
    left = _load_bundle(left_bundle)
    right = _load_bundle(right_bundle)

    left_plan = left["analyze"]["playback_plan"]
    right_plan = right["analyze"]["playback_plan"]
    left_arr = left["analyze"]["arrangement"]
    right_arr = right["analyze"]["arrangement"]
    left_metrics = left["run_metrics"].get("metrics", {})
    right_metrics = right["run_metrics"].get("metrics", {})

    deltas = {
        "event_group_count_delta": right_plan.get("event_group_count", 0) - left_plan.get("event_group_count", 0),
        "motor_change_count_delta": right_plan.get("motor_change_count", 0) - left_plan.get("motor_change_count", 0),
        "max_delta_us_delta": right_plan.get("max_delta_us", 0) - left_plan.get("max_delta_us", 0),
        "weighted_musical_loss_delta": round(
            float(right_arr.get("weighted_musical_loss", 0.0)) - float(left_arr.get("weighted_musical_loss", 0.0)),
            4,
        ),
        "motor_comfort_violation_count_delta": right_arr.get("motor_comfort_violation_count", 0)
        - left_arr.get("motor_comfort_violation_count", 0),
        "underrun_count_delta": int(right_metrics.get("underrun_count", 0)) - int(left_metrics.get("underrun_count", 0)),
        "scheduler_guard_hits_delta": int(right_metrics.get("scheduler_guard_hits", 0))
        - int(left_metrics.get("scheduler_guard_hits", 0)),
        "control_overrun_count_delta": int(right_metrics.get("control_overrun_count", 0))
        - int(left_metrics.get("control_overrun_count", 0)),
        "scheduling_late_max_us_delta": int(right_metrics.get("scheduling_late_max_us", 0))
        - int(left_metrics.get("scheduling_late_max_us", 0)),
    }

    likely_layer = "inconclusive"
    if deltas["underrun_count_delta"] > 0:
        likely_layer = "transport-layer"
    elif deltas["scheduler_guard_hits_delta"] > 0 or deltas["scheduling_late_max_us_delta"] > 0:
        likely_layer = "scheduler/runtime"
    elif deltas["control_overrun_count_delta"] > 0:
        likely_layer = "backend/motor-control"
    elif deltas["motor_comfort_violation_count_delta"] > 0:
        likely_layer = "calibration/profile"
    elif deltas["weighted_musical_loss_delta"] != 0:
        likely_layer = "configuration/operator-driven"

    regressions = [key for key, value in deltas.items() if value > 0]
    improvements = [key for key, value in deltas.items() if value < 0]
    warnings: list[str] = []
    left_profile = left["manifest"].get("provenance", {}).get("files", {}).get("instrument_profile", {})
    right_profile = right["manifest"].get("provenance", {}).get("files", {}).get("instrument_profile", {})
    if left_profile.get("sha256") and right_profile.get("sha256") and left_profile.get("sha256") != right_profile.get("sha256"):
        warnings.append("instrument profile fingerprint changed between bundles")
    left_git = left["manifest"].get("provenance", {}).get("git_commit_sha")
    right_git = right["manifest"].get("provenance", {}).get("git_commit_sha")
    if left_git and right_git and left_git != right_git:
        warnings.append("git commit changed between bundles")
    return {
        "left_bundle": str(Path(left_bundle).resolve()),
        "right_bundle": str(Path(right_bundle).resolve()),
        "deltas": deltas,
        "likely_regression_layer": likely_layer,
        "regressions": regressions,
        "improvements": improvements,
        "inconclusive": [key for key, value in deltas.items() if value == 0],
        "warnings": warnings,
    }


def render_benchmark_markdown_report(comparison: dict[str, Any]) -> str:
    lines = [
        "# Benchmark Comparison",
        "",
        f"- Base: `{comparison['left_bundle']}`",
        f"- Candidate: `{comparison['right_bundle']}`",
        f"- Likely layer: `{comparison['likely_regression_layer']}`",
        "",
        "## Deltas",
    ]
    for key, value in sorted(comparison["deltas"].items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Summary",
            f"- Improvements: {', '.join(comparison['improvements']) or 'none'}",
            f"- Regressions: {', '.join(comparison['regressions']) or 'none'}",
            f"- Inconclusive: {', '.join(comparison['inconclusive']) or 'none'}",
            f"- Warnings: {', '.join(comparison['warnings']) or 'none'}",
        ]
    )
    return "\n".join(lines) + "\n"
