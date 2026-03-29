#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import socket
import time
from pathlib import Path
from typing import Any

from music2.compiler import compile_segments
from music2.config import HostConfig, load_config
from music2.midi import analyze_midi
from music2.models import CompileOptions, Segment
from music2.protocol import (
    Ack,
    Command,
    Packet,
    decode_frame,
    encode_frame,
    encode_hello_payload,
    encode_setup_payload,
    encode_stream_append_payload,
    encode_stream_begin_payload,
    parse_ack,
    parse_err,
    parse_hello_ack,
    parse_metrics_payload,
    parse_status_payload,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProtocolSocket:
    def __init__(self, host: str, port: int, timeout_s: float = 0.25):
        self._host = host
        self._port = port
        self._timeout_s = timeout_s
        self._sock: socket.socket | None = None
        self._seq = 1
        self._rx = bytearray()

    def __enter__(self) -> "ProtocolSocket":
        sock = socket.create_connection((self._host, self._port), timeout=3.0)
        sock.settimeout(self._timeout_s)
        self._sock = sock
        self._flush_boot_noise()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def _flush_boot_noise(self) -> None:
        if self._sock is None:
            return
        end_at = time.monotonic() + 0.35
        while time.monotonic() < end_at:
            try:
                chunk = self._sock.recv(4096)
            except TimeoutError:
                break
            if not chunk:
                break
            self._rx.clear()

    def _next_seq(self) -> int:
        seq = self._seq
        self._seq = (self._seq + 1) & 0xFFFF
        if self._seq == 0:
            self._seq = 1
        return seq

    def _recv_packet(self, deadline: float) -> Packet:
        assert self._sock is not None
        while time.monotonic() < deadline:
            nul_idx = self._rx.find(0)
            if nul_idx != -1:
                raw = bytes(self._rx[: nul_idx + 1])
                del self._rx[: nul_idx + 1]
                try:
                    return decode_frame(raw)
                except Exception:
                    continue
            try:
                chunk = self._sock.recv(4096)
            except TimeoutError:
                continue
            if not chunk:
                raise RuntimeError("socket closed by qemu")
            self._rx.extend(chunk)
        raise TimeoutError("timed out waiting for protocol response")

    def request(self, command: Command, payload: bytes = b"", timeout_s: float = 1.0) -> Packet:
        assert self._sock is not None
        seq = self._next_seq()
        frame = encode_frame(command, seq=seq, payload=payload)
        self._sock.sendall(frame)
        deadline = time.monotonic() + timeout_s
        while True:
            packet = self._recv_packet(deadline)
            if packet.seq == seq:
                return packet



def _require_ack(packet: Packet, expected: Command) -> Ack:
    if packet.command == Command.ERR:
        err = parse_err(packet.payload)
        raise RuntimeError(
            f"device err for {expected.name}: cmd={err.for_command.name} code={err.error_code} "
            f"credits={err.credits} depth={err.queue_depth} message={err.message}"
        )
    if packet.command != Command.ACK:
        raise RuntimeError(f"expected ACK for {expected.name}, got {packet.command.name}")
    ack = parse_ack(packet.payload)
    if ack.for_command != expected:
        raise RuntimeError(f"ACK mismatch expected {expected.name} got {ack.for_command.name}")
    return ack


def _build_compile_options(cfg: HostConfig) -> CompileOptions:
    return CompileOptions(
        connected_motors=cfg.connected_motors,
        idle_mode=cfg.idle_mode,
        overflow_mode=cfg.overflow_mode,
        sticky_gap_s=cfg.sticky_gap_ms / 1000.0,
        segment_floor_us=cfg.segment_floor_us,
        segment_floor_pulse_budget=cfg.segment_floor_pulse_budget,
    )


def build_fur_elise_scenarios(cfg: HostConfig, midi_path: Path) -> tuple[dict[str, list[Segment]], dict[str, Any]]:
    analysis = analyze_midi(
        midi_path=midi_path,
        min_freq_hz=cfg.min_freq_hz,
        max_freq_hz=cfg.max_freq_hz,
        transpose_override=-22,
        auto_transpose=False,
    )
    compiled = compile_segments(analysis.notes, _build_compile_options(cfg))

    full = list(compiled.segments)

    first_window: list[Segment] = []
    us_accum = 0
    for seg in full:
        first_window.append(seg)
        us_accum += seg.duration_us
        if us_accum >= 20_000_000:
            break

    stress_slice: list[Segment] = []
    stress_us = 0
    for seg in full:
        hot = max(seg.motor_freq_hz, default=0.0) >= 650.0
        short = seg.duration_us <= 4000
        if not (hot or short):
            continue
        stress_seg = Segment(duration_us=max(1000, int(seg.duration_us * 0.35)), motor_freq_hz=seg.motor_freq_hz)
        stress_slice.append(stress_seg)
        stress_us += stress_seg.duration_us
        if stress_us >= 12_000_000:
            break

    meta = {
        "midi": str(midi_path),
        "transpose": -22,
        "analysis": {
            "notes": analysis.note_count,
            "max_polyphony": analysis.max_polyphony,
            "duration_s": analysis.duration_s,
            "clamped_notes": analysis.clamped_note_count,
        },
        "compile": {
            "segment_count": len(full),
            "adjacent_segments_merged": compiled.adjacent_segments_merged,
            "short_segments_absorbed": compiled.short_segments_absorbed,
            "stolen_note_count": compiled.stolen_note_count,
            "dropped_note_count": compiled.dropped_note_count,
        },
    }

    return {
        "fur_elise_20s": first_window,
        "fur_elise_hot_short_stress": stress_slice,
    }, meta


def build_synthetic_scenarios() -> dict[str, list[Segment]]:
    high_toggle: list[Segment] = []
    for i in range(3500):
        if i % 4 == 0:
            freqs = (790.0, 0.0, 760.0, 0.0, 730.0, 0.0)
            dur = 1200
        elif i % 4 == 1:
            freqs = (0.0, 790.0, 0.0, 760.0, 0.0, 730.0)
            dur = 1200
        elif i % 4 == 2:
            freqs = (800.0, 780.0, 760.0, 740.0, 720.0, 700.0)
            dur = 1500
        else:
            freqs = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
            dur = 1000
        high_toggle.append(Segment(duration_us=dur, motor_freq_hz=freqs))

    steady_high = [
        Segment(duration_us=4000, motor_freq_hz=(780.0, 760.0, 740.0, 720.0, 700.0, 680.0))
        for _ in range(1800)
    ]

    return {
        "synthetic_high_toggle": high_toggle,
        "synthetic_steady_high": steady_high,
    }


def _append_until_target(
    link: ProtocolSocket,
    segments: list[Segment],
    sent: int,
    credits: int,
    queue_depth: int,
    target_depth: int,
) -> tuple[int, int, int]:
    from music2.protocol import MAX_SEGMENTS_PER_APPEND

    while sent < len(segments) and credits > 0 and queue_depth < target_depth:
        chunk_count = min(MAX_SEGMENTS_PER_APPEND, credits, len(segments) - sent, max(1, target_depth - queue_depth))
        payload = encode_stream_append_payload(segments[sent : sent + chunk_count])
        ack = _require_ack(link.request(Command.STREAM_APPEND, payload), Command.STREAM_APPEND)
        sent += chunk_count
        queue_depth = max(0, ack.queue_depth if ack.queue_depth is not None else queue_depth + chunk_count)
        credits = max(0, ack.credits if ack.credits is not None else credits - chunk_count)
    return sent, credits, queue_depth


def run_scenario(link: ProtocolSocket, name: str, segments: list[Segment], motors: int) -> dict[str, Any]:
    hello_ack = _require_ack(link.request(Command.HELLO, encode_hello_payload("qemu-rca/0.1")), Command.HELLO)
    hello_info = parse_hello_ack(hello_ack)
    if hello_info is None:
        raise RuntimeError("missing hello info")

    requested = max(8, min(64, hello_info.queue_capacity))

    setup_payload = encode_setup_payload(
        motors=motors,
        idle_mode="idle",
        min_note=21,
        max_note=108,
        transpose=-22,
    )
    _require_ack(link.request(Command.SETUP, setup_payload), Command.SETUP)

    begin_payload = encode_stream_begin_payload(total_segments=len(segments), requested_credits=requested)
    begin_ack = _require_ack(link.request(Command.STREAM_BEGIN, begin_payload), Command.STREAM_BEGIN)
    credits = max(1, begin_ack.credits if begin_ack.credits is not None else requested)
    queue_depth = max(0, begin_ack.queue_depth if begin_ack.queue_depth is not None else 0)
    target_depth = max(8, min(credits + queue_depth, requested))

    sent = 0
    stream_end_sent = False
    sent, credits, queue_depth = _append_until_target(link, segments, sent, credits, queue_depth, target_depth)

    _require_ack(link.request(Command.PLAY), Command.PLAY)

    started = time.monotonic()
    status_samples: list[dict[str, int | bool]] = []
    while True:
        status_pkt = link.request(Command.STATUS, timeout_s=0.7)
        if status_pkt.command == Command.ERR:
            raise RuntimeError(f"status command failed: {parse_err(status_pkt.payload)}")
        if status_pkt.command != Command.STATUS:
            raise RuntimeError(f"expected STATUS packet, got {status_pkt.command.name}")
        status = parse_status_payload(status_pkt.payload)
        status_samples.append(
            {
                "playing": status.playing,
                "queue_depth": status.queue_depth,
                "credits": status.credits,
                "active_motors": status.active_motors,
                "playhead_us": status.playhead_us,
            }
        )

        sent, credits, queue_depth = _append_until_target(
            link,
            segments,
            sent,
            max(0, status.credits),
            max(0, status.queue_depth),
            target_depth,
        )

        if sent >= len(segments) and not stream_end_sent:
            _require_ack(link.request(Command.STREAM_END), Command.STREAM_END)
            stream_end_sent = True

        done = stream_end_sent and status.queue_depth == 0 and not status.playing
        if done:
            break

        if time.monotonic() - started > 60.0:
            raise TimeoutError(f"scenario {name} timed out waiting for playback completion")

        time.sleep(0.004)

    metrics_pkt = link.request(Command.METRICS, timeout_s=1.0)
    if metrics_pkt.command != Command.METRICS:
        raise RuntimeError(f"expected METRICS packet, got {metrics_pkt.command.name}")
    metrics = parse_metrics_payload(metrics_pkt.payload)

    _require_ack(link.request(Command.STOP), Command.STOP)

    duration_s = sum(seg.duration_us for seg in segments) / 1_000_000.0
    return {
        "scenario": name,
        "segments": len(segments),
        "duration_s": duration_s,
        "queue_capacity": hello_info.queue_capacity,
        "scheduler_tick_us": hello_info.scheduler_tick_us,
        "status_samples_tail": status_samples[-12:],
        "metrics": asdict(metrics),
        "segments_started_delta": metrics.segments_started - len(segments),
        "wall_time_s": round(time.monotonic() - started, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run closed-loop QEMU protocol diagnostics")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5555)
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--midi", default="assets/midi/Fur Elise.mid")
    parser.add_argument("--out", default=None)
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        help="Scenario name to run (repeatable). If omitted, run all scenarios.",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    fur_path = Path(args.midi).expanduser().resolve()
    if not fur_path.exists():
        raise FileNotFoundError(f"MIDI not found: {fur_path}")

    fur_scenarios, fur_meta = build_fur_elise_scenarios(cfg, fur_path)
    synth_scenarios = build_synthetic_scenarios()
    scenarios = {**fur_scenarios, **synth_scenarios}
    if args.only:
        wanted = set(args.only)
        scenarios = {name: segs for name, segs in scenarios.items() if name in wanted}
        if not scenarios:
            raise ValueError(f"--only requested unknown scenarios: {sorted(wanted)}")

    started_at = _now_iso()
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    with ProtocolSocket(args.host, args.port) as link:
        for run_idx in range(args.runs):
            for name, segments in scenarios.items():
                try:
                    row = run_scenario(link, name=name, segments=segments, motors=cfg.connected_motors)
                    row["run_index"] = run_idx + 1
                    rows.append(row)
                except Exception as exc:
                    failures.append({"run_index": run_idx + 1, "scenario": name, "error": str(exc)})

    payload = {
        "generated_at": _now_iso(),
        "started_at": started_at,
        "target": {"host": args.host, "port": args.port},
        "config": str(Path(args.config).expanduser().resolve()),
        "fur_elise_meta": fur_meta,
        "scenario_names": list(scenarios.keys()),
        "runs_requested": args.runs,
        "runs_succeeded": len(rows),
        "runs_failed": len(failures),
        "results": rows,
        "failures": failures,
    }

    if args.out:
        out_path = Path(args.out).expanduser().resolve()
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_path = (Path(".cache") / "rca_qemu" / f"{stamp}-closed-loop.json").resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
