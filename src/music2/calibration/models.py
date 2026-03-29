from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MeasurementObservation:
    label: str
    severity: float = 1.0
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("observation label cannot be empty")
        if self.severity < 0.0 or self.severity > 1.0:
            raise ValueError("observation severity must be in range [0, 1]")


@dataclass(frozen=True)
class MeasurementPoint:
    test_id: str
    motor_idx: int
    mode: str
    target_hz: float
    duration_s: float
    success: bool
    metrics: dict[str, int | float]
    observations: tuple[MeasurementObservation, ...] = ()
    accel_hz_per_s: float | None = None
    launch_start_hz: float | None = None
    launch_crossover_hz: float | None = None
    reversal_gap_ms: float | None = None

    def __post_init__(self) -> None:
        if not self.test_id.strip():
            raise ValueError("test_id cannot be empty")
        if self.motor_idx < 0 or self.motor_idx > 7:
            raise ValueError("motor_idx must be in range [0, 7]")
        if self.target_hz < 0.0:
            raise ValueError("target_hz must be >= 0")
        if self.duration_s <= 0.0:
            raise ValueError("duration_s must be > 0")


@dataclass(frozen=True)
class MeasurementSession:
    session_id: str
    session_type: str
    motors: tuple[int, ...]
    points: tuple[MeasurementPoint, ...]
    operator: str = ""
    firmware_version: str = ""
    hardware_notes: str = ""
    created_at_utc: str = ""
    transport_mode: str = "hardware"

    def __post_init__(self) -> None:
        if not self.session_id.strip():
            raise ValueError("session_id cannot be empty")
        if not self.points:
            raise ValueError("measurement session must contain at least one point")


@dataclass(frozen=True)
class CalibrationBandRecommendation:
    start_hz: float
    end_hz: float
    severity: float
    label: str
    source_measurement_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.start_hz < 0.0:
            raise ValueError("start_hz must be >= 0")
        if self.end_hz <= self.start_hz:
            raise ValueError("end_hz must be > start_hz")
        if self.severity < 0.0 or self.severity > 1.0:
            raise ValueError("severity must be in range [0, 1]")


@dataclass(frozen=True)
class MotorCalibrationRecommendation:
    motor_idx: int
    status: str
    confidence: float
    measured_min_hz: float | None = None
    measured_max_hz: float | None = None
    fitted_min_hz: float | None = None
    fitted_max_hz: float | None = None
    fitted_preferred_min_hz: float | None = None
    fitted_preferred_max_hz: float | None = None
    fitted_launch_start_hz: float | None = None
    fitted_launch_crossover_hz: float | None = None
    fitted_safe_reverse_min_gap_ms: float | None = None
    safe_accel_min_hz_per_s: float | None = None
    safe_accel_max_hz_per_s: float | None = None
    resonance_bands: tuple[CalibrationBandRecommendation, ...] = ()
    avoid_bands: tuple[CalibrationBandRecommendation, ...] = ()
    stall_prone_bands: tuple[CalibrationBandRecommendation, ...] = ()
    warnings: tuple[str, ...] = ()
    measurement_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.motor_idx < 0 or self.motor_idx > 7:
            raise ValueError("motor_idx must be in range [0, 7]")
        if self.confidence < 0.0 or self.confidence > 1.0:
            raise ValueError("confidence must be in range [0, 1]")


@dataclass(frozen=True)
class ProfilePatch:
    instrument_path: str
    motors: tuple[MotorCalibrationRecommendation, ...]
    generated_at_utc: str
    source_session_ids: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    metadata: dict[str, str | int | float] = field(default_factory=dict)

