#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
from pathlib import Path
import sys

if __package__ is None or __package__ == "":
    # Allow: python scripts/serial_motor_spin_test.py
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from music2.config import load_config
from music2.protocol import FEATURE_FLAG_STEP_MOTION, StepMotionMotorParams, StepMotionPhase
from music2.serial_client import DeviceError, SerialClient, SerialClientError


def _parse_motor_list(raw: str) -> tuple[int, ...]:
    if not raw.strip():
        raise ValueError("motor list must not be empty")
    return tuple(int(part.strip()) for part in raw.split(","))


def _build_motor_profile(
    *,
    active_motor: int,
    motor_slots: int,
    target_steps: int,
    peak_hz: float,
    accel_hz_per_s: float,
    decel_hz_per_s: float,
    hold_ms: int,
) -> list[StepMotionMotorParams]:
    params: list[StepMotionMotorParams] = []
    for idx in range(motor_slots):
        if idx == active_motor:
            phases = (
                StepMotionPhase(
                    target_steps=target_steps,
                    peak_hz=peak_hz,
                    accel_hz_per_s=accel_hz_per_s,
                    decel_hz_per_s=decel_hz_per_s,
                    hold_ms=hold_ms,
                ),
            )
        else:
            phases = (
                StepMotionPhase(
                    target_steps=0,
                    peak_hz=0.0,
                    accel_hz_per_s=accel_hz_per_s,
                    decel_hz_per_s=decel_hz_per_s,
                    hold_ms=0,
                ),
            )
        params.append(
            StepMotionMotorParams(
                phases=phases,
                start_delay_ms=0,
                trigger_motor=None,
                trigger_steps=0,
            )
        )
    return params


def _ramp_steps(peak_hz: float, accel_hz_per_s: float) -> float:
    if peak_hz <= 0.0 or accel_hz_per_s <= 0.0:
        return 0.0
    microstep_ratio = 16.0
    t_s = peak_hz / accel_hz_per_s
    return 0.5 * (peak_hz * microstep_ratio) * t_s


def _validate_profile_or_raise(*, steps: int, peak_hz: float, accel_hz_per_s: float, decel_hz_per_s: float) -> None:
    up = _ramp_steps(peak_hz, accel_hz_per_s)
    down = _ramp_steps(peak_hz, decel_hz_per_s)
    if (up + down) >= float(steps):
        limit = math.sqrt(
            float(steps)
            / (8.0 * ((1.0 / accel_hz_per_s) + (1.0 / decel_hz_per_s)))
        )
        raise ValueError(
            "profile is not feasible for exact step targeting: "
            f"ramp_steps≈{up + down:.1f} exceeds steps={steps}. "
            f"Lower peak_hz below about {limit:.1f} or raise accel/decel."
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Sequentially spin motors over Serial using music2 protocol with a trapezoidal profile."
        )
    )
    parser.add_argument("--config", default="config.toml", help="Path to config.toml")
    parser.add_argument("--port", default=None, help="Serial port override (defaults to config)")
    parser.add_argument("--baudrate", type=int, default=None, help="Serial baudrate override")
    parser.add_argument("--connected-motors", type=int, default=None, help="SETUP motors value override")
    parser.add_argument(
        "--motors",
        default="0,1,2,3,4,5",
        help="Comma-separated motor indices to test in sequence",
    )
    parser.add_argument("--steps", type=int, default=1600, help="Exact step count per motor")
    parser.add_argument("--peak-hz", type=float, default=260.0, help="Peak speed in Hz")
    parser.add_argument("--accel-hz-per-s", type=float, default=3000.0, help="Acceleration in Hz/s")
    parser.add_argument("--decel-hz-per-s", type=float, default=3000.0, help="Deceleration in Hz/s")
    parser.add_argument("--hold-ms", type=int, default=0, help="Minimum hold at peak before decel")
    parser.add_argument("--command-timeout-s", type=float, default=20.0, help="Timeout per motor step-motion command")
    parser.add_argument("--dry-run", action="store_true", help="Only print plan, do not send serial commands")
    args = parser.parse_args()

    cfg = load_config(args.config)
    port = args.port or cfg.port
    baudrate = args.baudrate or cfg.baudrate
    connected_motors = args.connected_motors or cfg.connected_motors
    motors = _parse_motor_list(args.motors)
    _validate_profile_or_raise(
        steps=args.steps,
        peak_hz=args.peak_hz,
        accel_hz_per_s=args.accel_hz_per_s,
        decel_hz_per_s=args.decel_hz_per_s,
    )
    motor_slots = max(connected_motors, max(motors) + 1)
    commanded_steps = {motor: args.steps for motor in motors}

    print("Spin test plan:")
    print(f"  motors: {motors}")
    print(f"  steps_per_motor: {args.steps}")
    print(f"  commanded_steps: {commanded_steps}")
    print(f"  motor_slots: {motor_slots}")
    print(f"  peak_hz: {args.peak_hz:.1f}")
    print(f"  accel_hz_per_s: {args.accel_hz_per_s:.1f}")
    print(f"  decel_hz_per_s: {args.decel_hz_per_s:.1f}")
    print(f"  hold_ms: {args.hold_ms}")
    print(f"  command_timeout_s: {args.command_timeout_s:.1f}")
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

        print(
            "Device hello:",
            {
                "motor_count": device_motor_count,
                "queue_capacity": int(hello.get("queue_capacity", 0)),
                "scheduler_tick_us": int(hello.get("scheduler_tick_us", 0)),
                "feature_flags": feature_flags,
            },
        )
        client.setup(
            motors=motor_slots,
            idle_mode=cfg.idle_mode,
            min_note=21,
            max_note=108,
            transpose=0,
        )
        for motor in motors:
            print(f"Running motor {motor} for {args.steps} steps...")
            profile = _build_motor_profile(
                active_motor=motor,
                motor_slots=motor_slots,
                target_steps=args.steps,
                peak_hz=args.peak_hz,
                accel_hz_per_s=args.accel_hz_per_s,
                decel_hz_per_s=args.decel_hz_per_s,
                hold_ms=args.hold_ms,
            )
            try:
                client.step_motion(profile, timeout_s=args.command_timeout_s)
                print(f"Motor {motor} complete.")
            except KeyboardInterrupt:
                print("\nInterrupted; aborting without waiting for STOP ACK.")
                raise
            except DeviceError as exc:
                raise RuntimeError(
                    f"STEP_MOTION failed on motor {motor}: command={exc.command.name} "
                    f"code={exc.code} credits={exc.credits} queue_depth={exc.queue_depth}"
                ) from exc
            except SerialClientError as exc:
                raise RuntimeError(
                    f"serial motion failed on motor {motor}: {exc}"
                ) from exc
        metrics = client.metrics()
        status = client.status()

    print("Playback complete.")
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
