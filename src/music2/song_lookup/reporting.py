from __future__ import annotations

from .types import LookupResult


def format_lookup_result(result: LookupResult) -> str:
    lines = [
        f"Song: {result.query.title}" + (f" · {result.query.artist}" if result.query.artist else ""),
        "",
    ]
    if result.warnings:
        lines.append("Warnings:")
        for warning in result.warnings:
            lines.append(f"  - {warning}")
        lines.append("")

    if not result.candidates:
        lines.append("No candidates found.")
        return "\n".join(lines)

    lines.append("Ranked candidates:")
    for idx, candidate in enumerate(result.candidates, start=1):
        prefix = "*" if result.recommended_index == idx - 1 else " "
        summary = (
            f"{prefix} {idx}. {candidate.source_hit.title}"
            f" [{candidate.source_hit.source_name}/{candidate.source_hit.source_kind}]"
            f" score={candidate.score:.2f}"
        )
        lines.append(summary)
        if candidate.analysis is not None:
            lines.append(
                "     "
                f"poly={candidate.analysis.max_polyphony} "
                f"drop={candidate.analysis.allocation_dropped_note_count} "
                f"loss={candidate.analysis.weighted_musical_loss:.2f} "
                f"comfort={candidate.analysis.motor_comfort_violation_count}"
            )
            if candidate.analysis.exported_motor_safe_midi is not None:
                lines.append(f"     motor-safe: {candidate.analysis.exported_motor_safe_midi}")
        if candidate.warnings:
            for warning in candidate.warnings:
                lines.append(f"     warn: {warning}")
    return "\n".join(lines)
