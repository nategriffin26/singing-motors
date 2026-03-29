from __future__ import annotations

from dataclasses import dataclass

from ..instrument_profile import InstrumentMotorProfile


@dataclass(frozen=True)
class BackendConstraintResult:
    entered_risk_band: bool
    risk_tags: tuple[str, ...]


class SimplifiedMotorBackend:
    def classify(self, motor: InstrumentMotorProfile, target_hz: float) -> BackendConstraintResult:
        tags: list[str] = []
        if target_hz < motor.resolved_min_hz or target_hz > motor.resolved_max_hz:
            tags.append("out_of_range")
        if target_hz < motor.resolved_preferred_min_hz or target_hz > motor.resolved_preferred_max_hz:
            tags.append("outside_preferred_band")
        if any(band.start_hz <= target_hz <= band.end_hz for band in motor.resonance_bands):
            tags.append("resonance_band")
        if any(band.start_hz <= target_hz <= band.end_hz for band in motor.avoid_bands):
            tags.append("avoid_band")
        if any(band.start_hz <= target_hz <= band.end_hz for band in motor.stall_prone_bands):
            tags.append("stall_prone_band")
        return BackendConstraintResult(
            entered_risk_band=bool(tags),
            risk_tags=tuple(tags),
        )
