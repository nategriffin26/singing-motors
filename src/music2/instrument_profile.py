from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INSTRUMENT_PROFILE_PATH = (_PROJECT_ROOT / "profiles" / "default_instrument.toml").resolve()


@dataclass(frozen=True)
class FrequencyBand:
    start_hz: float
    end_hz: float
    severity: float = 1.0
    label: str = ""

    def __post_init__(self) -> None:
        if self.start_hz < 0.0:
            raise ValueError("band start_hz must be >= 0")
        if self.end_hz <= self.start_hz:
            raise ValueError("band end_hz must be > start_hz")
        if self.severity < 0.0 or self.severity > 1.0:
            raise ValueError("band severity must be in range [0, 1]")


@dataclass(frozen=True)
class InstrumentMotorProfile:
    motor_idx: int
    label: str
    min_hz: float
    max_hz: float
    preferred_min_hz: float | None = None
    preferred_max_hz: float | None = None
    launch_start_hz: float | None = None
    launch_crossover_hz: float | None = None
    safe_reverse_min_gap_ms: float = 0.0
    safe_reverse_margin_ms: float = 0.0
    resonance_bands: tuple[FrequencyBand, ...] = ()
    avoid_bands: tuple[FrequencyBand, ...] = ()
    stall_prone_bands: tuple[FrequencyBand, ...] = ()
    weight_pitch_stability: float = 1.0
    weight_attack_cleanliness: float = 1.0
    weight_sustain_quality: float = 1.0
    calibration_status: str = "legacy"
    calibration_confidence: float | None = None
    measured_min_hz: float | None = None
    measured_max_hz: float | None = None
    fitted_min_hz: float | None = None
    fitted_max_hz: float | None = None
    override_min_hz: float | None = None
    override_max_hz: float | None = None
    measured_preferred_min_hz: float | None = None
    measured_preferred_max_hz: float | None = None
    fitted_preferred_min_hz: float | None = None
    fitted_preferred_max_hz: float | None = None
    override_preferred_min_hz: float | None = None
    override_preferred_max_hz: float | None = None
    measured_launch_start_hz: float | None = None
    measured_launch_crossover_hz: float | None = None
    fitted_launch_start_hz: float | None = None
    fitted_launch_crossover_hz: float | None = None
    override_launch_start_hz: float | None = None
    override_launch_crossover_hz: float | None = None
    measured_safe_reverse_min_gap_ms: float | None = None
    fitted_safe_reverse_min_gap_ms: float | None = None
    override_safe_reverse_min_gap_ms: float | None = None
    safe_accel_min_hz_per_s: float | None = None
    safe_accel_max_hz_per_s: float | None = None
    reversal_tolerance_ms: float | None = None
    calibration_measurement_date: str = ""
    calibration_firmware_version: str = ""
    calibration_operator: str = ""
    calibration_hardware_notes: str = ""
    calibration_measurement_ids: tuple[str, ...] = ()
    operator_notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.motor_idx < 0 or self.motor_idx > 7:
            raise ValueError("motor_idx must be in range [0, 7]")
        if not self.label.strip():
            object.__setattr__(self, "label", f"motor_{self.motor_idx}")
        if self.min_hz <= 0.0:
            raise ValueError("motor min_hz must be > 0")
        if self.max_hz <= self.min_hz:
            raise ValueError("motor max_hz must be > min_hz")

        preferred_min_hz = self.min_hz if self.preferred_min_hz is None else float(self.preferred_min_hz)
        preferred_max_hz = self.max_hz if self.preferred_max_hz is None else float(self.preferred_max_hz)
        if preferred_min_hz < self.min_hz:
            raise ValueError("preferred_min_hz must be >= min_hz")
        if preferred_max_hz > self.max_hz:
            raise ValueError("preferred_max_hz must be <= max_hz")
        if preferred_max_hz < preferred_min_hz:
            raise ValueError("preferred_max_hz must be >= preferred_min_hz")
        object.__setattr__(self, "preferred_min_hz", preferred_min_hz)
        object.__setattr__(self, "preferred_max_hz", preferred_max_hz)

        launch_start_hz = preferred_min_hz if self.launch_start_hz is None else float(self.launch_start_hz)
        launch_crossover_hz = preferred_max_hz if self.launch_crossover_hz is None else float(self.launch_crossover_hz)
        if launch_start_hz <= 0.0:
            raise ValueError("launch_start_hz must be > 0")
        if launch_crossover_hz < launch_start_hz:
            raise ValueError("launch_crossover_hz must be >= launch_start_hz")
        if launch_crossover_hz > self.max_hz:
            raise ValueError("launch_crossover_hz must be <= max_hz")
        object.__setattr__(self, "launch_start_hz", launch_start_hz)
        object.__setattr__(self, "launch_crossover_hz", launch_crossover_hz)

        if self.safe_reverse_min_gap_ms < 0.0:
            raise ValueError("safe_reverse_min_gap_ms must be >= 0")
        if self.safe_reverse_margin_ms < 0.0:
            raise ValueError("safe_reverse_margin_ms must be >= 0")
        if self.calibration_confidence is not None and (self.calibration_confidence < 0.0 or self.calibration_confidence > 1.0):
            raise ValueError("calibration_confidence must be in range [0, 1]")
        if self.safe_accel_min_hz_per_s is not None and self.safe_accel_min_hz_per_s < 0.0:
            raise ValueError("safe_accel_min_hz_per_s must be >= 0")
        if self.safe_accel_max_hz_per_s is not None and self.safe_accel_max_hz_per_s < 0.0:
            raise ValueError("safe_accel_max_hz_per_s must be >= 0")
        if (
            self.safe_accel_min_hz_per_s is not None
            and self.safe_accel_max_hz_per_s is not None
            and self.safe_accel_max_hz_per_s < self.safe_accel_min_hz_per_s
        ):
            raise ValueError("safe_accel_max_hz_per_s must be >= safe_accel_min_hz_per_s")
        if self.reversal_tolerance_ms is not None and self.reversal_tolerance_ms < 0.0:
            raise ValueError("reversal_tolerance_ms must be >= 0")
        if self.weight_pitch_stability <= 0.0:
            raise ValueError("weight_pitch_stability must be > 0")
        if self.weight_attack_cleanliness <= 0.0:
            raise ValueError("weight_attack_cleanliness must be > 0")
        if self.weight_sustain_quality <= 0.0:
            raise ValueError("weight_sustain_quality must be > 0")

    @staticmethod
    def _first_present(*values: float | None) -> float | None:
        for value in values:
            if value is not None:
                return float(value)
        return None

    @property
    def resolved_min_hz(self) -> float:
        return float(self._first_present(self.override_min_hz, self.fitted_min_hz, self.measured_min_hz, self.min_hz))

    @property
    def resolved_max_hz(self) -> float:
        return float(self._first_present(self.override_max_hz, self.fitted_max_hz, self.measured_max_hz, self.max_hz))

    @property
    def resolved_preferred_min_hz(self) -> float:
        return float(
            self._first_present(
                self.override_preferred_min_hz,
                self.fitted_preferred_min_hz,
                self.measured_preferred_min_hz,
                self.preferred_min_hz,
                self.resolved_min_hz,
            )
        )

    @property
    def resolved_preferred_max_hz(self) -> float:
        return float(
            self._first_present(
                self.override_preferred_max_hz,
                self.fitted_preferred_max_hz,
                self.measured_preferred_max_hz,
                self.preferred_max_hz,
                self.resolved_max_hz,
            )
        )

    @property
    def resolved_launch_start_hz(self) -> float:
        return float(
            self._first_present(
                self.override_launch_start_hz,
                self.fitted_launch_start_hz,
                self.measured_launch_start_hz,
                self.launch_start_hz,
                self.resolved_preferred_min_hz,
            )
        )

    @property
    def resolved_launch_crossover_hz(self) -> float:
        return float(
            self._first_present(
                self.override_launch_crossover_hz,
                self.fitted_launch_crossover_hz,
                self.measured_launch_crossover_hz,
                self.launch_crossover_hz,
                self.resolved_preferred_max_hz,
            )
        )

    @property
    def resolved_safe_reverse_min_gap_ms(self) -> float:
        candidate = self._first_present(
            self.override_safe_reverse_min_gap_ms,
            self.fitted_safe_reverse_min_gap_ms,
            self.measured_safe_reverse_min_gap_ms,
            self.reversal_tolerance_ms,
            self.safe_reverse_min_gap_ms,
        )
        return float(candidate if candidate is not None else 0.0)

    @property
    def has_calibration_data(self) -> bool:
        return any(
            value is not None
            for value in (
                self.measured_min_hz,
                self.measured_max_hz,
                self.fitted_min_hz,
                self.fitted_max_hz,
                self.override_min_hz,
                self.override_max_hz,
                self.safe_accel_min_hz_per_s,
                self.safe_accel_max_hz_per_s,
            )
        ) or bool(self.calibration_measurement_ids)


@dataclass(frozen=True)
class InstrumentProfile:
    name: str
    profile_version: int
    motor_count: int
    motors: tuple[InstrumentMotorProfile, ...]
    description: str = ""
    source_path: Path | None = None
    calibration_schema_version: int = 1
    calibration_measurement_date: str = ""
    calibration_firmware_version: str = ""
    calibration_operator: str = ""
    calibration_hardware_notes: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("instrument profile name cannot be empty")
        if self.profile_version < 1:
            raise ValueError("profile_version must be >= 1")
        if self.motor_count < 1 or self.motor_count > 8:
            raise ValueError("motor_count must be in range [1, 8]")
        if len(self.motors) != self.motor_count:
            raise ValueError("motor_count must match the number of motor entries")
        expected = set(range(self.motor_count))
        actual = {motor.motor_idx for motor in self.motors}
        if actual != expected:
            raise ValueError(f"motor indices must exactly match {sorted(expected)}")

    @property
    def ordered_motors(self) -> tuple[InstrumentMotorProfile, ...]:
        return tuple(sorted(self.motors, key=lambda motor: motor.motor_idx))


def resolve_instrument_profile_path(
    profile_path: str | Path,
    *,
    base_dir: str | Path | None = None,
) -> Path:
    candidate = Path(profile_path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    if base_dir is not None:
        return (Path(base_dir).expanduser() / candidate).resolve()
    return candidate.resolve()


def _parse_band_list(value: object, *, field_name: str) -> tuple[FrequencyBand, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    bands: list[FrequencyBand] = []
    for idx, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"{field_name}[{idx}] must be a table")
        bands.append(
            FrequencyBand(
                start_hz=float(item.get("start_hz", 0.0)),
                end_hz=float(item.get("end_hz", 0.0)),
                severity=float(item.get("severity", 1.0)),
                label=str(item.get("label", "")),
            )
        )
    return tuple(bands)


def _parse_str_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        normalized = value.strip()
        return (normalized,) if normalized else ()
    if not isinstance(value, list):
        raise ValueError(f"expected string list, got: {value!r}")
    out: list[str] = []
    for item in value:
        normalized = str(item).strip()
        if normalized:
            out.append(normalized)
    return tuple(out)


def _parse_motor_profile(raw: object, *, idx: int) -> InstrumentMotorProfile:
    if not isinstance(raw, dict):
        raise ValueError(f"instrument.motors[{idx}] must be a table")
    return InstrumentMotorProfile(
        motor_idx=int(raw.get("motor_idx", idx)),
        label=str(raw.get("label", f"motor_{idx}")),
        min_hz=float(raw.get("min_hz", 0.0)),
        max_hz=float(raw.get("max_hz", 0.0)),
        preferred_min_hz=float(raw["preferred_min_hz"]) if "preferred_min_hz" in raw else None,
        preferred_max_hz=float(raw["preferred_max_hz"]) if "preferred_max_hz" in raw else None,
        launch_start_hz=float(raw["launch_start_hz"]) if "launch_start_hz" in raw else None,
        launch_crossover_hz=float(raw["launch_crossover_hz"]) if "launch_crossover_hz" in raw else None,
        safe_reverse_min_gap_ms=float(raw.get("safe_reverse_min_gap_ms", 0.0)),
        safe_reverse_margin_ms=float(raw.get("safe_reverse_margin_ms", 0.0)),
        resonance_bands=_parse_band_list(raw.get("resonance_bands"), field_name="resonance_bands"),
        avoid_bands=_parse_band_list(raw.get("avoid_bands"), field_name="avoid_bands"),
        stall_prone_bands=_parse_band_list(raw.get("stall_prone_bands"), field_name="stall_prone_bands"),
        weight_pitch_stability=float(raw.get("weight_pitch_stability", 1.0)),
        weight_attack_cleanliness=float(raw.get("weight_attack_cleanliness", 1.0)),
        weight_sustain_quality=float(raw.get("weight_sustain_quality", 1.0)),
        calibration_status=str(raw.get("calibration_status", "legacy")),
        calibration_confidence=float(raw["calibration_confidence"]) if "calibration_confidence" in raw else None,
        measured_min_hz=float(raw["measured_min_hz"]) if "measured_min_hz" in raw else None,
        measured_max_hz=float(raw["measured_max_hz"]) if "measured_max_hz" in raw else None,
        fitted_min_hz=float(raw["fitted_min_hz"]) if "fitted_min_hz" in raw else None,
        fitted_max_hz=float(raw["fitted_max_hz"]) if "fitted_max_hz" in raw else None,
        override_min_hz=float(raw["override_min_hz"]) if "override_min_hz" in raw else None,
        override_max_hz=float(raw["override_max_hz"]) if "override_max_hz" in raw else None,
        measured_preferred_min_hz=float(raw["measured_preferred_min_hz"]) if "measured_preferred_min_hz" in raw else None,
        measured_preferred_max_hz=float(raw["measured_preferred_max_hz"]) if "measured_preferred_max_hz" in raw else None,
        fitted_preferred_min_hz=float(raw["fitted_preferred_min_hz"]) if "fitted_preferred_min_hz" in raw else None,
        fitted_preferred_max_hz=float(raw["fitted_preferred_max_hz"]) if "fitted_preferred_max_hz" in raw else None,
        override_preferred_min_hz=float(raw["override_preferred_min_hz"]) if "override_preferred_min_hz" in raw else None,
        override_preferred_max_hz=float(raw["override_preferred_max_hz"]) if "override_preferred_max_hz" in raw else None,
        measured_launch_start_hz=float(raw["measured_launch_start_hz"]) if "measured_launch_start_hz" in raw else None,
        measured_launch_crossover_hz=float(raw["measured_launch_crossover_hz"]) if "measured_launch_crossover_hz" in raw else None,
        fitted_launch_start_hz=float(raw["fitted_launch_start_hz"]) if "fitted_launch_start_hz" in raw else None,
        fitted_launch_crossover_hz=float(raw["fitted_launch_crossover_hz"]) if "fitted_launch_crossover_hz" in raw else None,
        override_launch_start_hz=float(raw["override_launch_start_hz"]) if "override_launch_start_hz" in raw else None,
        override_launch_crossover_hz=float(raw["override_launch_crossover_hz"]) if "override_launch_crossover_hz" in raw else None,
        measured_safe_reverse_min_gap_ms=float(raw["measured_safe_reverse_min_gap_ms"])
        if "measured_safe_reverse_min_gap_ms" in raw
        else None,
        fitted_safe_reverse_min_gap_ms=float(raw["fitted_safe_reverse_min_gap_ms"])
        if "fitted_safe_reverse_min_gap_ms" in raw
        else None,
        override_safe_reverse_min_gap_ms=float(raw["override_safe_reverse_min_gap_ms"])
        if "override_safe_reverse_min_gap_ms" in raw
        else None,
        safe_accel_min_hz_per_s=float(raw["safe_accel_min_hz_per_s"]) if "safe_accel_min_hz_per_s" in raw else None,
        safe_accel_max_hz_per_s=float(raw["safe_accel_max_hz_per_s"]) if "safe_accel_max_hz_per_s" in raw else None,
        reversal_tolerance_ms=float(raw["reversal_tolerance_ms"]) if "reversal_tolerance_ms" in raw else None,
        calibration_measurement_date=str(raw.get("calibration_measurement_date", "")),
        calibration_firmware_version=str(raw.get("calibration_firmware_version", "")),
        calibration_operator=str(raw.get("calibration_operator", "")),
        calibration_hardware_notes=str(raw.get("calibration_hardware_notes", "")),
        calibration_measurement_ids=_parse_str_tuple(raw.get("calibration_measurement_ids")),
        operator_notes=_parse_str_tuple(raw.get("operator_notes")),
    )


def load_instrument_profile(path: str | Path) -> InstrumentProfile:
    profile_path = resolve_instrument_profile_path(path)
    if not profile_path.exists():
        raise FileNotFoundError(f"instrument profile not found: {profile_path}")

    with profile_path.open("rb") as handle:
        raw = tomllib.load(handle)

    instrument = raw.get("instrument")
    if not isinstance(instrument, dict):
        raise ValueError("instrument profile must contain an [instrument] table")

    motors_raw = instrument.get("motors")
    if not isinstance(motors_raw, list):
        raise ValueError("instrument profile must contain [[instrument.motors]] entries")

    motors = tuple(_parse_motor_profile(raw_motor, idx=idx) for idx, raw_motor in enumerate(motors_raw))
    return InstrumentProfile(
        name=str(instrument.get("name", "")).strip(),
        profile_version=int(instrument.get("profile_version", 1)),
        motor_count=int(instrument.get("motor_count", len(motors))),
        motors=motors,
        description=str(instrument.get("description", "")),
        source_path=profile_path,
        calibration_schema_version=int(instrument.get("calibration_schema_version", 1)),
        calibration_measurement_date=str(instrument.get("calibration_measurement_date", "")),
        calibration_firmware_version=str(instrument.get("calibration_firmware_version", "")),
        calibration_operator=str(instrument.get("calibration_operator", "")),
        calibration_hardware_notes=str(instrument.get("calibration_hardware_notes", "")),
    )
