#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
from pathlib import Path
import sys

if __package__ is None or __package__ == "":
    # Allow: python scripts/serial_motor_stress_test.py
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from music2.config import load_config
from music2.protocol import FEATURE_FLAG_HOME, FEATURE_FLAG_STEP_MOTION, StepMotionMotorParams, StepMotionPhase
from music2.serial_client import DeviceError, SerialClient, SerialClientError

_MICROSTEP_RATIO = 16.0


def _parse_motor_list(raw: str) -> tuple[int, ...]:
    if not raw.strip():
        raise ValueError("motor list must not be empty")
    motors = tuple(int(part.strip()) for part in raw.split(","))
    if len(set(motors)) != len(motors):
        raise ValueError("motor list contains duplicates")
    return motors


def _default_motor_order(*, connected_motors: int, config_order: tuple[int, ...]) -> tuple[int, ...]:
    if connected_motors < 1:
        raise ValueError("connected_motors must be >= 1")
    ordered: list[int] = []
    seen: set[int] = set()
    for motor in config_order:
        if 0 <= motor < connected_motors and motor not in seen:
            ordered.append(motor)
            seen.add(motor)
    for motor in range(connected_motors):
        if motor not in seen:
            ordered.append(motor)
            seen.add(motor)
    return tuple(ordered)


def _resolve_motor_order(
    *,
    connected_motors: int,
    config_order: tuple[int, ...],
    override: str | None,
) -> tuple[int, ...]:
    if override is None:
        return _default_motor_order(connected_motors=connected_motors, config_order=config_order)
    motors = _parse_motor_list(override)
    for motor in motors:
        if motor < 0 or motor >= connected_motors:
            raise ValueError(f"motor index out of range [0, {connected_motors - 1}]: {motor}")
    return motors


def _ramp_steps(peak_hz: float, accel_hz_per_s: float) -> float:
    if peak_hz <= 0.0:
        return 0.0
    if accel_hz_per_s <= 0.0:
        raise ValueError("accel/decel must be > 0 when peak_hz > 0")
    t_s = peak_hz / accel_hz_per_s
    return 0.5 * (peak_hz * _MICROSTEP_RATIO) * t_s


def _minimum_triangle_steps(*, peak_hz: float, accel_hz_per_s: float, decel_hz_per_s: float) -> int:
    if peak_hz <= 0.0:
        return 0
    total = _ramp_steps(peak_hz, accel_hz_per_s) + _ramp_steps(peak_hz, decel_hz_per_s)
    return max(1, int(math.ceil(total)))


def _phase_peak_sequence(*, min_hz: float, max_hz: float) -> tuple[float, ...]:
    if math.isclose(min_hz, max_hz, rel_tol=1e-9, abs_tol=1e-9):
        return (max_hz,)
    return (min_hz, max_hz)


def _build_stress_phases(
    *,
    min_hz: float,
    max_hz: float,
    slow_accel_hz_per_s: float,
    slow_decel_hz_per_s: float,
    fast_accel_hz_per_s: float,
    fast_decel_hz_per_s: float,
    hold_ms: int,
    steps_per_rev: int,
) -> tuple[tuple[StepMotionPhase, ...], int]:
    peaks = _phase_peak_sequence(min_hz=min_hz, max_hz=max_hz)
    phase_specs: list[tuple[float, float, float]] = []
    for peak in peaks:
        phase_specs.append((peak, slow_accel_hz_per_s, slow_decel_hz_per_s))
    for peak in peaks:
        phase_specs.append((peak, fast_accel_hz_per_s, fast_decel_hz_per_s))
    if not phase_specs:
        raise ValueError("no phases generated")
    if len(phase_specs) > 4:
        raise ValueError("stress profile exceeds STEP_MOTION phase limit (4)")

    target_steps = [
        _minimum_triangle_steps(peak_hz=peak, accel_hz_per_s=accel, decel_hz_per_s=decel)
        for peak, accel, decel in phase_specs
    ]
    for idx, steps in enumerate(target_steps):
        if steps > 0xFFFF:
            raise ValueError(
                f"phase {idx} requires {steps} steps; lower peak_hz or increase accel/decel to stay under 65535"
            )

    align_added_steps = 0
    if steps_per_rev > 0:
        remainder = sum(target_steps) % steps_per_rev
        if remainder:
            align_added_steps = steps_per_rev - remainder
            last = target_steps[-1]
            if (last + align_added_steps) > 0xFFFF:
                raise ValueError(
                    "cannot align profile to full-revolution step count without overflowing uint16; "
                    "increase accel/decel or lower peak_hz"
                )
            target_steps[-1] = last + align_added_steps

    phases = tuple(
        StepMotionPhase(
            target_steps=steps,
            peak_hz=peak,
            accel_hz_per_s=accel,
            decel_hz_per_s=decel,
            hold_ms=hold_ms,
        )
        for steps, (peak, accel, decel) in zip(target_steps, phase_specs, strict=True)
    )
    return phases, align_added_steps


def _build_motor_profile(
    *,
    active_motor: int,
    motor_slots: int,
    active_phases: tuple[StepMotionPhase, ...],
) -> list[StepMotionMotorParams]:
    idle = StepMotionPhase(
        target_steps=0,
        peak_hz=0.0,
        accel_hz_per_s=1.0,
        decel_hz_per_s=1.0,
        hold_ms=0,
    )
    profile: list[StepMotionMotorParams] = []
    for motor_idx in range(motor_slots):
        phases = active_phases if motor_idx == active_motor else (idle,)
        profile.append(
            StepMotionMotorParams(
                phases=phases,
                start_delay_ms=0,
                trigger_motor=None,
                trigger_steps=0,
            )
        )
    return profile


def _phase_duration_s(phase: StepMotionPhase) -> float:
    accel_s = (phase.peak_hz / phase.accel_hz_per_s) if phase.accel_hz_per_s > 0.0 else 0.0
    decel_s = (phase.peak_hz / phase.decel_hz_per_s) if phase.decel_hz_per_s > 0.0 else 0.0
    hold_s = max(0.0, float(phase.hold_ms) / 1000.0)
    return accel_s + hold_s + decel_s


def _supports_home(feature_flags: int) -> bool:
    return (feature_flags & FEATURE_FLAG_HOME) != 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Per-motor STEP_MOTION stress test: slow + fast full-range sweeps, "
            "in config order, with optional HOME before/after."
        )
    )
    parser.add_argument("--config", default="config.toml", help="Path to config.toml")
    parser.add_argument("--port", default=None, help="Serial port override (defaults to config)")
    parser.add_argument("--baudrate", type=int, default=None, help="Serial baudrate override")
    parser.add_argument("--connected-motors", type=int, default=None, help="SETUP motors value override")
    parser.add_argument(
        "--motors",
        default=None,
        help=(
            "Optional comma-separated motor order override. "
            "Default uses [warmups].motor_order then appends missing motors."
        ),
    )
    parser.add_argument("--min-hz", type=float, default=None, help="Minimum test frequency (defaults to config min)")
    parser.add_argument("--max-hz", type=float, default=None, help="Maximum test frequency (defaults to config max)")
    parser.add_argument("--slow-accel-hz-per-s", type=float, default=220.0, help="Slow sweep accel in Hz/s")
    parser.add_argument("--slow-decel-hz-per-s", type=float, default=220.0, help="Slow sweep decel in Hz/s")
    parser.add_argument("--fast-accel-hz-per-s", type=float, default=2800.0, help="Fast sweep accel in Hz/s")
    parser.add_argument("--fast-decel-hz-per-s", type=float, default=2800.0, help="Fast sweep decel in Hz/s")
    parser.add_argument("--hold-ms", type=int, default=0, help="Optional hold at each peak (ms)")
    parser.add_argument("--cycles-per-motor", type=int, default=1, help="How many stress cycles to run per motor")
    parser.add_argument(
        "--home-steps-per-rev",
        type=int,
        default=None,
        help="Homing steps/rev + alignment basis (defaults to config homing steps_per_rev)",
    )
    parser.add_argument("--home-before", dest="home_before", action="store_true", default=True)
    parser.add_argument("--no-home-before", dest="home_before", action="store_false")
    parser.add_argument("--home-after", dest="home_after", action="store_true", default=True)
    parser.add_argument("--no-home-after", dest="home_after", action="store_false")
    parser.add_argument("--command-timeout-s", type=float, default=45.0, help="Timeout per STEP_MOTION command")
    parser.add_argument("--dry-run", action="store_true", help="Only print plan, do not send serial commands")
    args = parser.parse_args()

    cfg = load_config(args.config)
    port = cfg.port if args.port is None else args.port
    baudrate = cfg.baudrate if args.baudrate is None else args.baudrate
    connected_motors = cfg.connected_motors if args.connected_motors is None else args.connected_motors
    if connected_motors < 1 or connected_motors > 8:
        raise ValueError("connected_motors must be in range [1, 8]")
    if args.cycles_per_motor < 1:
        raise ValueError("cycles_per_motor must be >= 1")
    if args.command_timeout_s <= 0.0:
        raise ValueError("command_timeout_s must be > 0")
    if args.hold_ms < 0:
        raise ValueError("hold_ms must be >= 0")

    min_hz = float(cfg.min_freq_hz if args.min_hz is None else args.min_hz)
    max_hz = float(cfg.max_freq_hz if args.max_hz is None else args.max_hz)
    if min_hz <= 0.0:
        raise ValueError("min_hz must be > 0")
    if max_hz <= 0.0:
        raise ValueError("max_hz must be > 0")
    if min_hz > max_hz:
        raise ValueError("min_hz must be <= max_hz")

    for label, value in (
        ("slow_accel_hz_per_s", args.slow_accel_hz_per_s),
        ("slow_decel_hz_per_s", args.slow_decel_hz_per_s),
        ("fast_accel_hz_per_s", args.fast_accel_hz_per_s),
        ("fast_decel_hz_per_s", args.fast_decel_hz_per_s),
    ):
        if value <= 0.0:
            raise ValueError(f"{label} must be > 0")

    home_steps_per_rev = cfg.home_steps_per_rev if args.home_steps_per_rev is None else args.home_steps_per_rev
    if home_steps_per_rev < 1:
        raise ValueError("home_steps_per_rev must be >= 1")

    motors = _resolve_motor_order(
        connected_motors=connected_motors,
        config_order=cfg.warmup_motor_order,
        override=args.motors,
    )
    motor_slots = max(connected_motors, max(motors) + 1)
    phases, align_added_steps = _build_stress_phases(
        min_hz=min_hz,
        max_hz=max_hz,
        slow_accel_hz_per_s=args.slow_accel_hz_per_s,
        slow_decel_hz_per_s=args.slow_decel_hz_per_s,
        fast_accel_hz_per_s=args.fast_accel_hz_per_s,
        fast_decel_hz_per_s=args.fast_decel_hz_per_s,
        hold_ms=args.hold_ms,
        steps_per_rev=home_steps_per_rev,
    )
    estimated_command_duration_s = sum(_phase_duration_s(phase) for phase in phases)

    print("Per-motor stress plan:")
    print(f"  motors: {motors}")
    print(f"  motor_slots: {motor_slots}")
    print(f"  config_warmup_order: {cfg.warmup_motor_order or '(none)'}")
    print(f"  min_hz: {min_hz:.1f}")
    print(f"  max_hz: {max_hz:.1f}")
    print(f"  slow_accel_hz_per_s: {args.slow_accel_hz_per_s:.1f}")
    print(f"  slow_decel_hz_per_s: {args.slow_decel_hz_per_s:.1f}")
    print(f"  fast_accel_hz_per_s: {args.fast_accel_hz_per_s:.1f}")
    print(f"  fast_decel_hz_per_s: {args.fast_decel_hz_per_s:.1f}")
    print(f"  hold_ms: {args.hold_ms}")
    print(f"  cycles_per_motor: {args.cycles_per_motor}")
    print(f"  home_steps_per_rev: {home_steps_per_rev}")
    print(f"  alignment_added_steps: {align_added_steps}")
    print(f"  profile_total_steps: {sum(phase.target_steps for phase in phases)}")
    print(f"  est_command_duration_s: {estimated_command_duration_s:.2f}")
    print(f"  command_timeout_s: {args.command_timeout_s:.1f}")
    print(f"  home_before: {args.home_before}")
    print(f"  home_after: {args.home_after}")
    for idx, phase in enumerate(phases):
        print(
            f"  phase[{idx}]: target_steps={phase.target_steps} peak_hz={phase.peak_hz:.1f} "
            f"accel={phase.accel_hz_per_s:.1f} decel={phase.decel_hz_per_s:.1f} hold_ms={phase.hold_ms}"
        )
    if args.dry_run:
        return 0

    with SerialClient(
        port=port,
        baudrate=baudrate,
        timeout_s=cfg.timeout_s,
        write_timeout_s=cfg.write_timeout_s,
        retries=cfg.retries,
    ) as client:
        hello = client.hello()
        feature_flags = int(hello.get("feature_flags", 0))
        if (feature_flags & FEATURE_FLAG_STEP_MOTION) == 0:
            raise RuntimeError(
                "firmware does not advertise STEP_MOTION support (feature flag 0x08). "
                "Flash updated firmware before running this test."
            )
        device_motor_count = int(hello.get("motor_count", 8))
        if max(motors) >= device_motor_count:
            raise RuntimeError(
                f"requested motor index {max(motors)} but device reports {device_motor_count} motors"
            )
        if max(motors) >= motor_slots:
            raise RuntimeError(
                f"connected_motors={motor_slots} must be greater than highest requested motor index {max(motors)}"
            )

        home_supported = _supports_home(feature_flags)
        print(
            "Device hello:",
            {
                "motor_count": device_motor_count,
                "queue_capacity": int(hello.get("queue_capacity", 0)),
                "scheduler_tick_us": int(hello.get("scheduler_tick_us", 0)),
                "feature_flags": feature_flags,
                "home_supported": home_supported,
            },
        )
        client.setup(
            motors=motor_slots,
            idle_mode=cfg.idle_mode,
            min_note=21,
            max_note=108,
            transpose=0,
        )

        if args.home_before:
            if home_supported:
                print("Homing before stress run...")
                client.home(
                    steps_per_rev=home_steps_per_rev,
                    home_hz=cfg.home_hz,
                    start_hz=cfg.home_start_hz,
                    accel_hz_per_s=cfg.home_accel_hz_per_s,
                )
            else:
                print("HOME not supported by firmware; skipping initial home.")

        for motor in motors:
            for cycle in range(args.cycles_per_motor):
                print(
                    f"Running motor {motor} cycle {cycle + 1}/{args.cycles_per_motor} "
                    f"({len(phases)} phases)..."
                )
                profile = _build_motor_profile(
                    active_motor=motor,
                    motor_slots=motor_slots,
                    active_phases=phases,
                )
                try:
                    client.step_motion(profile, timeout_s=args.command_timeout_s)
                except KeyboardInterrupt:
                    print("\nInterrupted; aborting without waiting for STOP ACK.")
                    raise
                except DeviceError as exc:
                    raise RuntimeError(
                        f"STEP_MOTION failed on motor {motor}: command={exc.command.name} "
                        f"code={exc.code} credits={exc.credits} queue_depth={exc.queue_depth}"
                    ) from exc
                except SerialClientError as exc:
                    raise RuntimeError(f"serial motion failed on motor {motor}: {exc}") from exc
                print(f"Motor {motor} cycle {cycle + 1} complete.")

        if args.home_after:
            if home_supported:
                print("Homing after stress run...")
                client.home(
                    steps_per_rev=home_steps_per_rev,
                    home_hz=cfg.home_hz,
                    start_hz=cfg.home_start_hz,
                    accel_hz_per_s=cfg.home_accel_hz_per_s,
                )
            else:
                print("HOME not supported by firmware; skipping final home.")

        metrics = client.metrics()
        status = client.status()

    print("Stress run complete.")
    print(
        "Final telemetry:",
        {
            "queue_depth": status.queue_depth,
            "playing": status.playing,
            "active_motors": status.active_motors,
            "underrun_count": metrics.underrun_count,
            "pulse_edge_drop_count": metrics.pulse_edge_drop_count,
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
