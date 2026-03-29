from __future__ import annotations

import argparse
import math
import os
import json
import platform
import select
import re
import shutil
import statistics
import sysconfig
import sys
import time
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterable, Iterator, Literal

from .arrangement_report import build_arrangement_report
from .compiler import AllocationError, compile_segments
from .config import HostConfig, load_config
from .instrument_profile import InstrumentProfile, load_instrument_profile, resolve_instrument_profile_path
from .midi import TempoMap, _fold_frequency, analyze_midi, midi_note_to_freq
from .models import (
    ArrangementReport,
    CompileOptions,
    CompileReport,
    IdleMode,
    LookaheadStrategy,
    MidiAnalysisReport,
    NoteEvent,
    OverflowMode,
    PlaybackMetrics,
    Segment,
    StreamStatus,
)
from .playback_modes import build_default_playback_program
from .playback_program import PlaybackPlan, PlaybackProgram
from .playback_runner import PlaybackRunner, attempt_interrupt_auto_home
from .protocol import (
    EXACT_MOTION_FEATURE_DIRECTIONAL_STEP_MOTION,
    FEATURE_FLAG_CONTINUOUS_PLAYBACK_ENGINE,
    FEATURE_FLAG_DIRECTION_FLIP,
    FEATURE_FLAG_HOME,
    FEATURE_FLAG_PLAYBACK_SETUP_PROFILE,
    FEATURE_FLAG_SPEECH_ASSIST,
    FEATURE_FLAG_STEP_MOTION,
    FEATURE_FLAG_TIMED_STREAMING,
    FEATURE_FLAG_WARMUP,
    StepMotionMotorParams,
)
from .render_wav import RenderWavOptions, render_midi_to_stepper_wav
from .runtime_observers import CallbackPlaybackObserver, CompositePlaybackObserver, DashboardObserver
from .serial_client import SerialClient, SerialClientError, StreamProgress
from .sim.compare import compare_plan_to_replay
from .sim.program_runner import simulate_playback_program
from .sim.replay import import_run_bundle
from .speech_text import (
    DEFAULT_CORPUS_PATH,
    available_preset_ids,
    compile_utterance,
    espeak_available,
    evaluate_render,
    load_corpus,
    load_speech_config,
    load_speech_preset,
    render_speech_to_wav,
    summarize_corpus,
    utterance_from_phonemes_file,
    utterance_from_text,
)
from .viewer_theme import THEME_IDS, ThemeId, coerce_theme_id
from .warmups import (
    WarmupMotorParams,
    build_warmup_params,
    build_warmup_step_motion_params,
)


# ---------------------------------------------------------------------------
# ANSI helpers
# ---------------------------------------------------------------------------

_USE_COLOR = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


class _S:
    RESET = "\033[0m" if _USE_COLOR else ""
    BOLD = "\033[1m" if _USE_COLOR else ""
    DIM = "\033[2m" if _USE_COLOR else ""
    RED = "\033[31m" if _USE_COLOR else ""
    GREEN = "\033[32m" if _USE_COLOR else ""
    YELLOW = "\033[33m" if _USE_COLOR else ""
    CYAN = "\033[36m" if _USE_COLOR else ""
    BLUE = "\033[34m" if _USE_COLOR else ""
    MAGENTA = "\033[35m" if _USE_COLOR else ""
    WHITE = "\033[37m" if _USE_COLOR else ""
    CLEAR_LINE = "\033[2K" if _USE_COLOR else ""
    CURSOR_UP = "\033[A" if _USE_COLOR else ""


def _out(text: str = "", end: str = "\n") -> None:
    sys.stdout.write(text + end)
    sys.stdout.flush()


def _fmt_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def _bar(fraction: float, width: int = 30) -> str:
    fraction = max(0.0, min(1.0, fraction))
    filled = int(fraction * width)
    return f"{_S.GREEN}{'█' * filled}{_S.DIM}{'░' * (width - filled)}{_S.RESET}"


def _row(label: str, value: str) -> None:
    _out(f"  {_S.DIM}{label:<16}{_S.RESET}{value}")


def _heading(title: str) -> None:
    _out(f"\n  {_S.BOLD}{_S.CYAN}{title}{_S.RESET}")


# ---------------------------------------------------------------------------
# Panel builder – titled box-drawn panels with section dividers
# ---------------------------------------------------------------------------

class _Panel:
    """Collects rows and sections, then renders as a bordered panel."""

    def __init__(self, *, title: str = "", width: int | None = None) -> None:
        self._title = title
        self._lines: list[str] = []  # plain-text lines (may contain ANSI)
        self._width = width or _panel_content_width()

    def blank(self) -> None:
        self._lines.append("")

    def section(self, heading: str) -> None:
        self.blank()
        pad = max(0, self._width - len(heading) - 1)
        self._lines.append(f"{_S.BOLD}{_S.CYAN}{heading}{_S.RESET} {_S.DIM}{'─' * pad}{_S.RESET}")

    def row(self, label: str, value: str) -> None:
        self._lines.append(f"{_S.DIM}{label:<16}{_S.RESET}{value}")

    def raw(self, text: str) -> None:
        self._lines.append(text)

    def divider(self) -> None:
        self._lines.append(f"{_S.DIM}{'─' * self._width}{_S.RESET}")

    def render(self) -> list[str]:
        w = self._width
        border = f"{_S.DIM}"
        reset = _S.RESET

        if self._title:
            title_str = f" {self._title} "
            pad = max(0, w + 2 - len(title_str) - 1)
            top = f"{border}╭──{reset}{_S.BOLD}{_S.CYAN}{title_str}{_S.RESET}{border}{'─' * pad}╮{reset}"
        else:
            top = f"{border}╭{'─' * (w + 2)}╮{reset}"

        bottom = f"{border}╰{'─' * (w + 2)}╯{reset}"
        body: list[str] = []
        for line in self._lines:
            clipped = _clip_text(line, w)
            plain_len = len(_strip_ansi(line))
            ansi_overhead = len(line) - plain_len
            padded = line[:w + ansi_overhead] if plain_len <= w else clipped
            visible = len(_strip_ansi(padded))
            padding = max(0, w - visible)
            body.append(f"{border}│{reset} {padded}{' ' * padding} {border}│{reset}")
        return [top, *body, bottom]

    def emit(self) -> None:
        _out()
        for line in self.render():
            _out(f"  {line}")
        _out()


def _fmt_freq_range(min_freq_hz: float | None, max_freq_hz: float | None) -> str:
    if min_freq_hz is None or max_freq_hz is None:
        return "n/a"
    return f"{min_freq_hz:.1f} - {max_freq_hz:.1f} Hz"


def _fmt_note_range(min_note: int | None, max_note: int | None) -> str:
    if min_note is None or max_note is None:
        return "n/a"
    min_freq_hz = midi_note_to_freq(min_note)
    max_freq_hz = midi_note_to_freq(max_note)
    return f"{min_note} - {max_note}  {_S.DIM}({min_freq_hz:.1f} - {max_freq_hz:.1f} Hz){_S.RESET}"


def _transpose_mode_label(cfg: HostConfig) -> str:
    if cfg.transpose_override is not None:
        return f"manual override ({cfg.transpose_override:+d})"
    if cfg.auto_transpose:
        return "auto"
    return "fixed (0 semitones)"


def _preflight_stats(analysis: MidiAnalysisReport, cfg: HostConfig) -> dict[str, int | float | None]:
    if not analysis.notes:
        return {
            "min_transposed_note": None,
            "max_transposed_note": None,
            "raw_min_freq_hz": None,
            "raw_max_freq_hz": None,
            "output_min_freq_hz": None,
            "output_max_freq_hz": None,
            "clamped_below_min": 0,
            "clamped_above_max": 0,
            "clamped_pct": 0.0,
        }

    min_transposed_note = min(note.transposed_note for note in analysis.notes)
    max_transposed_note = max(note.transposed_note for note in analysis.notes)
    raw_freqs = [midi_note_to_freq(note.transposed_note) for note in analysis.notes]
    output_freqs = [note.frequency_hz for note in analysis.notes]
    clamped_below_min = sum(1 for freq in raw_freqs if freq < cfg.min_freq_hz)
    clamped_above_max = sum(1 for freq in raw_freqs if freq > cfg.max_freq_hz)
    clamped_pct = (analysis.clamped_note_count / analysis.note_count * 100.0) if analysis.note_count else 0.0
    return {
        "min_transposed_note": min_transposed_note,
        "max_transposed_note": max_transposed_note,
        "raw_min_freq_hz": min(raw_freqs),
        "raw_max_freq_hz": max(raw_freqs),
        "output_min_freq_hz": min(output_freqs),
        "output_max_freq_hz": max(output_freqs),
        "clamped_below_min": clamped_below_min,
        "clamped_above_max": clamped_above_max,
        "clamped_pct": clamped_pct,
    }


def _float_percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    if len(sorted_values) == 1:
        return sorted_values[0]
    clamped_pct = max(0.0, min(100.0, pct))
    idx = (len(sorted_values) - 1) * (clamped_pct / 100.0)
    low = int(idx)
    high = min(len(sorted_values) - 1, low + 1)
    alpha = idx - low
    return (1.0 - alpha) * sorted_values[low] + alpha * sorted_values[high]


def _sparkline(values: list[float], width: int) -> str:
    if width <= 0:
        return ""
    if not values:
        return "·" * width
    if len(values) == 1:
        return "▄" * width
    if len(values) <= width:
        sampled = values + [values[-1]] * (width - len(values))
    else:
        step = (len(values) - 1) / max(1, width - 1)
        sampled = [values[int(round(idx * step))] for idx in range(width)]
    low = min(sampled)
    high = max(sampled)
    if abs(high - low) < 1e-9:
        return "▄" * width
    glyphs = "▁▂▃▄▅▆▇█"
    scale = len(glyphs) - 1
    return "".join(glyphs[int(round((value - low) / (high - low) * scale))] for value in sampled)


def _chart_width() -> int:
    cols = shutil.get_terminal_size((100, 20)).columns
    return max(40, min(72, cols - 18))


def _frequency_histogram_lines(
    *,
    analysis: MidiAnalysisReport,
    cfg: HostConfig,
    width: int,
    height: int = 8,
) -> list[str]:
    commanded_freqs = [note.frequency_hz for note in analysis.notes if note.frequency_hz > 0.0]
    raw_freqs = [midi_note_to_freq(note.transposed_note) for note in analysis.notes]
    if not commanded_freqs and not raw_freqs:
        return [f"{_S.DIM}(no note frequencies to visualize){_S.RESET}"]

    domain_values = [cfg.min_freq_hz, cfg.max_freq_hz, *commanded_freqs, *raw_freqs]
    domain_min = min(domain_values)
    domain_max = max(domain_values)
    if domain_max <= domain_min:
        domain_max = domain_min + 1.0
    span = domain_max - domain_min

    plot_width = max(24, width)
    plot_height = max(4, min(12, height))
    bins = [0 for _ in range(plot_width)]

    def to_idx(freq_hz: float) -> int:
        ratio = (freq_hz - domain_min) / span
        return max(0, min(plot_width - 1, int(round(ratio * (plot_width - 1)))))

    for freq_hz in commanded_freqs:
        bins[to_idx(freq_hz)] += 1

    max_count = max(bins) if bins else 0
    max_count = max(max_count, 1)
    min_cap_idx = to_idx(cfg.min_freq_hz)
    max_cap_idx = to_idx(cfg.max_freq_hz)

    lines: list[str] = []
    for row in range(plot_height, 0, -1):
        threshold = row / plot_height
        chars: list[str] = []
        for idx, count in enumerate(bins):
            filled = (count / max_count) >= threshold
            is_cap = idx == min_cap_idx or idx == max_cap_idx
            if filled and is_cap:
                chars.append("╋")
            elif filled:
                chars.append("█")
            elif is_cap:
                chars.append("│")
            else:
                chars.append(" ")
        lines.append("".join(chars))

    axis = ["─" for _ in range(plot_width)]
    axis[min_cap_idx] = "┼"
    axis[max_cap_idx] = "┼"
    lines.append("".join(axis))

    left = f"{domain_min:>6.1f} Hz"
    right = f"{domain_max:>6.1f} Hz"
    gap = max(1, plot_width - len(left) - len(right))
    lines.append(f"{left}{' ' * gap}{right}")
    lines.append(f"{_S.DIM}caps: min {cfg.min_freq_hz:.1f} Hz, max {cfg.max_freq_hz:.1f} Hz{_S.RESET}")
    lines.append(f"{_S.DIM}bars: commanded note frequencies (post-clamp){_S.RESET}")
    lines.append(
        f"{_S.DIM}timeline:{_S.RESET} {_sparkline(commanded_freqs or raw_freqs, plot_width)}"
    )
    return lines


def _render_run_summary(
    *,
    args: argparse.Namespace,
    cfg: HostConfig,
    instrument_profile: InstrumentProfile,
    arrangement_report: ArrangementReport,
    midi_path: Path,
    analysis: MidiAnalysisReport,
    compiled: CompileReport,
    avg_active: float,
) -> None:
    preflight = _preflight_stats(analysis, cfg)
    commanded_freqs = [note.frequency_hz for note in analysis.notes if note.frequency_hz > 0.0]
    clamped_count = analysis.clamped_note_count
    clamped_pct = float(preflight["clamped_pct"])
    transpose_val = f"{analysis.transpose_semitones:+d} semitones" if analysis.transpose_semitones else "none"
    if clamped_count:
        transpose_val += f"  {_S.YELLOW}({clamped_count} clamped){_S.RESET}"
    else:
        transpose_val += f"  {_S.DIM}(0 clamped){_S.RESET}"

    p = _Panel(title="music2 · playback preflight")

    p.section("Song")
    p.row("File", midi_path.name)
    p.row("Duration", _fmt_time(analysis.duration_s))
    p.row("Notes", f"{analysis.note_count:,}  {_S.DIM}(polyphony: {analysis.max_polyphony}){_S.RESET}")
    p.row("Transpose", transpose_val)
    p.row("Transpose mode", _transpose_mode_label(cfg))
    p.row("Target band", f"{cfg.min_freq_hz:.1f} – {cfg.max_freq_hz:.1f} Hz")

    p.section("Preflight")
    p.row("Source notes", _fmt_note_range(analysis.min_source_note, analysis.max_source_note))
    p.row(
        "Shifted notes",
        _fmt_note_range(
            int(preflight["min_transposed_note"]) if preflight["min_transposed_note"] is not None else None,
            int(preflight["max_transposed_note"]) if preflight["max_transposed_note"] is not None else None,
        ),
    )
    p.row(
        "Raw span",
        _fmt_freq_range(
            float(preflight["raw_min_freq_hz"]) if preflight["raw_min_freq_hz"] is not None else None,
            float(preflight["raw_max_freq_hz"]) if preflight["raw_max_freq_hz"] is not None else None,
        ),
    )
    p.row(
        "Output span",
        _fmt_freq_range(
            float(preflight["output_min_freq_hz"]) if preflight["output_min_freq_hz"] is not None else None,
            float(preflight["output_max_freq_hz"]) if preflight["output_max_freq_hz"] is not None else None,
        ),
    )
    p.row(
        "Clamped notes",
        (
            f"{clamped_count:,}/{analysis.note_count:,} ({clamped_pct:.1f}%)"
            f"  {_S.DIM}[below: {int(preflight['clamped_below_min'])}, above: {int(preflight['clamped_above_max'])}]{_S.RESET}"
        ),
    )
    if commanded_freqs:
        p.row(
            "Command dist",
            (
                f"median {_float_percentile(commanded_freqs, 50):.1f} Hz, "
                f"p90 {_float_percentile(commanded_freqs, 90):.1f} Hz, "
                f"p99 {_float_percentile(commanded_freqs, 99):.1f} Hz"
            ),
        )
    if clamped_count > 0:
        p.row("Clamp policy", f"{_S.YELLOW}octave fold into target band{_S.RESET}")

    p.section("Frequency View")
    chart_w = max(24, p._width - 4)
    chart_lines = _frequency_histogram_lines(analysis=analysis, cfg=cfg, width=chart_w)
    for line in chart_lines:
        p.raw(line)

    p.section("Pipeline")
    if getattr(args, "profile", None):
        p.row("Profile", str(getattr(args, "profile")))
    p.row(
        "Instrument",
        (
            f"{instrument_profile.name}"
            f"  {_S.DIM}({cfg.connected_motors}/{instrument_profile.motor_count} motors, "
            f"v{instrument_profile.profile_version}){_S.RESET}"
        ),
    )
    p.row("Motors", f"{cfg.connected_motors}  {_S.DIM}({cfg.idle_mode} idle){_S.RESET}")
    p.row("Overflow", cfg.overflow_mode)
    p.row(
        "Allocation",
        (
            f"retained {analysis.note_count - compiled.dropped_note_count:,}/{analysis.note_count:,}"
            f"  {_S.DIM}(steals {compiled.stolen_note_count:,}, drops {compiled.dropped_note_count:,}){_S.RESET}"
        ),
    )
    risk_hints: list[str] = []
    if compiled.stolen_note_count > 0 or compiled.dropped_note_count > 0:
        risk_hints.append("polyphony overflow active")
    if compiled.truncated_note_count > 0:
        risk_hints.append(
            f"{compiled.truncated_note_count} notes truncated"
            f" ({compiled.zero_length_note_count} to zero)"
        )
    if compiled.tight_boundary_warning_count > 0:
        risk_hints.append(f"{compiled.tight_boundary_warning_count} tight playback boundaries")
    if risk_hints:
        p.row("Risk hints", f"{_S.YELLOW}{'; '.join(risk_hints)}{_S.RESET}")
    if compiled.direction_flip_requested_count > 0:
        p.row(
            "Dir flips",
            (
                f"requested {compiled.direction_flip_requested_count:,}"
                f"  {_S.DIM}(applied {compiled.direction_flip_applied_count:,}, "
                f"suppressed {compiled.direction_flip_suppressed_count:,}, "
                f"cooldown {compiled.direction_flip_cooldown_suppressed_count:,}){_S.RESET}"
            ),
        )
    p.row(
        "Melody loss",
        (
            f"{arrangement_report.dropped_melody_note_count:,}/{arrangement_report.melody_note_count:,} dropped"
            f"  {_S.DIM}(bass {arrangement_report.dropped_bass_note_count:,}, "
            f"inner {arrangement_report.dropped_inner_note_count:,}){_S.RESET}"
        ),
    )
    p.row(
        "Physicality",
        (
            f"retarget {arrangement_report.octave_retargeted_note_count:,}, "
            f"avoid flips {arrangement_report.avoided_reversal_count:,}, "
            f"comfort hits {arrangement_report.motor_comfort_violation_count:,}"
        ),
    )
    p.row("Weighted loss", f"{arrangement_report.weighted_musical_loss:.2f}")
    p.row(
        "Event groups",
        f"{len(compiled.event_groups):,}  {_S.DIM}(avg {avg_active:.1f} active){_S.RESET}",
    )
    p.row("Motor changes", f"{compiled.motor_change_count:,}")
    p.row(
        "Lookahead",
        (
            f"{cfg.lookahead_ms} ms ({cfg.lookahead_strategy}, floor {cfg.lookahead_min_ms} ms, "
            f"min {cfg.lookahead_min_segments} groups)"
        ),
    )
    p.row("UI sync offset", f"{cfg.ui_sync_offset_ms:+.1f} ms")
    p.row("Port", f"{cfg.port}  {_S.DIM}@ {cfg.baudrate:,}{_S.RESET}")
    p.blank()

    p.emit()


def _supports_transpose_studio() -> bool:
    if not (hasattr(sys.stdin, "isatty") and sys.stdin.isatty()):
        return False
    if not (hasattr(sys.stdout, "isatty") and sys.stdout.isatty()):
        return False
    if sys.platform == "win32":
        return False
    try:
        sys.stdin.fileno()
    except (OSError, ValueError):
        return False
    try:
        import termios  # noqa: F401
        import tty  # noqa: F401
    except Exception:
        return False
    return True


@contextmanager
def _raw_stdin_mode(fd: int) -> Iterator[None]:
    import termios
    import tty

    prior = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, prior)


def _read_keypress(fd: int) -> str:
    chunk = os.read(fd, 1)
    if not chunk:
        return ""
    if chunk != b"\x1b":
        return chunk.decode("utf-8", errors="ignore")
    seq = bytearray(chunk)
    for _ in range(2):
        ready, _write_ready, _err = select.select([fd], [], [], 0.015)
        if not ready:
            break
        seq.extend(os.read(fd, 1))
    return seq.decode("utf-8", errors="ignore")


def _transpose_delta_from_keypress(key: str) -> int | None:
    if key in {"\x1b[A", "\x1b[C", "k", "K", "+", "="}:
        return 1
    if key in {"\x1b[B", "\x1b[D", "j", "J", "-", "_"}:
        return -1
    return None


def _retranspose_analysis(
    base_analysis: MidiAnalysisReport,
    new_transpose: int,
    min_freq_hz: float,
    max_freq_hz: float,
) -> MidiAnalysisReport:
    """Re-transpose notes from an existing analysis without re-parsing MIDI.

    Only does arithmetic on the already-parsed note list — no file I/O,
    no segment compilation.  Used for instant transpose-studio previews.
    """
    delta = new_transpose - base_analysis.transpose_semitones
    notes: list[NoteEvent] = []
    clamped_count = 0
    for note in base_analysis.notes:
        transposed_note = note.source_note + new_transpose
        unclamped_freq = midi_note_to_freq(transposed_note)
        final_freq, clamped = _fold_frequency(unclamped_freq, min_freq_hz, max_freq_hz)
        clamped_count += int(clamped)
        notes.append(
            NoteEvent(
                start_s=note.start_s,
                end_s=note.end_s,
                source_note=note.source_note,
                transposed_note=transposed_note,
                frequency_hz=final_freq,
                velocity=note.velocity,
                channel=note.channel,
                source_track=note.source_track,
                source_track_name=note.source_track_name,
            )
        )
    return MidiAnalysisReport(
        notes=notes,
        duration_s=base_analysis.duration_s,
        note_count=base_analysis.note_count,
        max_polyphony=base_analysis.max_polyphony,
        transpose_semitones=new_transpose,
        clamped_note_count=clamped_count,
        min_source_note=base_analysis.min_source_note,
        max_source_note=base_analysis.max_source_note,
    )


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _clip_text(text: str, width: int) -> str:
    plain = _strip_ansi(text)
    if width <= 0:
        return ""
    if len(plain) <= width:
        return plain
    if width == 1:
        return "…"
    return plain[: width - 1] + "…"


def _panel_content_width() -> int:
    cols = shutil.get_terminal_size((100, 20)).columns
    return max(24, min(96, cols - 4))


def _to_index(value: float, lo: float, hi: float, width: int, *, axis: Literal["linear", "log"] = "linear") -> int:
    if hi <= lo or width <= 1:
        return 0

    if axis == "log" and lo > 0.0 and hi > 0.0 and value > 0.0:
        lo_t = math.log10(lo)
        hi_t = math.log10(hi)
        value_t = math.log10(value)
    else:
        lo_t = lo
        hi_t = hi
        value_t = value

    if hi_t <= lo_t:
        return 0

    ratio = (value_t - lo_t) / (hi_t - lo_t)
    return max(0, min(width - 1, int(round(ratio * (width - 1)))))


def _sparkline_hist(
    values: list[float],
    width: int,
    lo: float,
    hi: float,
    *,
    axis: Literal["linear", "log"] = "linear",
) -> str:
    if width <= 0:
        return ""
    if not values:
        return " " * width
    bins = [0 for _ in range(width)]
    for value in values:
        bins[_to_index(value, lo, hi, width, axis=axis)] += 1
    max_count = max(bins) if bins else 0
    if max_count <= 0:
        return " " * width
    glyphs = " ▁▂▃▄▅▆▇█"
    scale = len(glyphs) - 1
    return "".join(glyphs[int(round((count / max_count) * scale))] for count in bins)


def _occupancy_line(
    values: list[float],
    width: int,
    lo: float,
    hi: float,
    *,
    axis: Literal["linear", "log"] = "linear",
) -> str:
    if width <= 0:
        return ""
    chars = [" " for _ in range(width)]
    for value in values:
        chars[_to_index(value, lo, hi, width, axis=axis)] = "•"
    return "".join(chars)


def _cap_rail_line(
    width: int,
    lo: float,
    hi: float,
    min_cap: float,
    max_cap: float,
    *,
    axis: Literal["linear", "log"] = "linear",
) -> str:
    if width <= 0:
        return ""
    left = _to_index(min_cap, lo, hi, width, axis=axis)
    right = _to_index(max_cap, lo, hi, width, axis=axis)
    if left > right:
        left, right = right, left
    chars = ["─" for _ in range(width)]
    for idx in range(left + 1, right):
        chars[idx] = "═"
    chars[left] = "╞"
    chars[right] = "╡"
    return "".join(chars)


def _titled_box(lines: list[str], width: int, title: str = "") -> list[str]:
    """Render lines inside a titled box-drawn panel (used by transpose studio)."""
    safe_width = max(24, width)
    border = _S.DIM
    reset = _S.RESET
    if title:
        title_str = f" {title} "
        pad = max(0, safe_width + 2 - len(title_str) - 1)
        top = f"{border}╭──{reset}{_S.BOLD}{_S.CYAN}{title_str}{reset}{border}{'─' * pad}╮{reset}"
    else:
        top = f"{border}╭{'─' * (safe_width + 2)}╮{reset}"
    bottom = f"{border}╰{'─' * (safe_width + 2)}╯{reset}"
    body: list[str] = []
    for line in lines:
        clipped = _clip_text(line, safe_width)
        plain_len = len(_strip_ansi(line))
        ansi_overhead = len(line) - plain_len
        padded = line[:safe_width + ansi_overhead] if plain_len <= safe_width else clipped
        visible = len(_strip_ansi(padded))
        padding = max(0, safe_width - visible)
        body.append(f"{border}│{reset} {padded}{' ' * padding} {border}│{reset}")
    return [top, *body, bottom]


def _build_transpose_studio_lines(
    *,
    cfg: HostConfig,
    midi_path: Path,
    analysis: MidiAnalysisReport,
    compiled: CompileReport,
    avg_active: float,
    base_transpose: int,
    status_message: str,
    preview: bool = False,
) -> list[str]:
    preflight = _preflight_stats(analysis, cfg)
    delta = analysis.transpose_semitones - base_transpose
    clamped_pct = float(preflight["clamped_pct"])
    commanded_freqs = [note.frequency_hz for note in analysis.notes if note.frequency_hz > 0.0]
    raw_freqs = [midi_note_to_freq(note.transposed_note) for note in analysis.notes]
    domain_values = [cfg.min_freq_hz, cfg.max_freq_hz, *commanded_freqs, *raw_freqs]
    domain_min = min(domain_values) if domain_values else cfg.min_freq_hz
    domain_max = max(domain_values) if domain_values else cfg.max_freq_hz
    if domain_max <= domain_min:
        domain_max = domain_min + 1.0

    panel_width = _panel_content_width()
    chart_width = max(28, panel_width - 16)
    hist_line = _sparkline_hist(commanded_freqs, chart_width, domain_min, domain_max, axis="log")
    raw_line = _occupancy_line(raw_freqs, chart_width, domain_min, domain_max, axis="log")
    cap_line = _cap_rail_line(
        chart_width,
        domain_min,
        domain_max,
        cfg.min_freq_hz,
        cfg.max_freq_hz,
        axis="log",
    )

    output_span = (
        _fmt_freq_range(float(preflight["output_min_freq_hz"]), float(preflight["output_max_freq_hz"]))
        if preflight["output_min_freq_hz"] is not None and preflight["output_max_freq_hz"] is not None
        else "n/a"
    )

    # Clamp color: green if 0%, yellow if <5%, red if >=5%
    clamp_color = _S.GREEN if clamped_pct == 0 else (_S.YELLOW if clamped_pct < 5 else _S.RED)

    body: list[str] = [
        "",
        f"{_S.DIM}  arrows ±1 semitone  ·  Enter lock-in  ·  q cancel{_S.RESET}",
        "",
        f"{_S.DIM}{'─' * panel_width}{_S.RESET}",
        f"{_S.DIM}Song{' ' * 12}{_S.RESET}{midi_path.name}",
        (
            f"{_S.DIM}Selected{' ' * 8}{_S.RESET}"
            f"{_S.BOLD}{analysis.transpose_semitones:+d} st{_S.RESET}"
            f"  {_S.DIM}(base {base_transpose:+d}, delta {delta:+d}){_S.RESET}"
        ),
        (
            f"{_S.DIM}Clamped{' ' * 9}{_S.RESET}"
            f"{clamp_color}{analysis.clamped_note_count:,}/{analysis.note_count:,} ({clamped_pct:.1f}%){_S.RESET}"
        ),
        (
            f"{_S.DIM}Event groups{' ' * 4}"
            f"{'~' if preview else ''}{len(compiled.event_groups):,}"
            f"  ·  avg active {'~' if preview else ''}{avg_active:.1f}{_S.RESET}"
            if preview
            else (
                f"{_S.DIM}Event groups{' ' * 4}{_S.RESET}{len(compiled.event_groups):,}"
                f"  {_S.DIM}·{_S.RESET}  avg active {avg_active:.1f}"
            )
        ),
        f"{_S.DIM}Output span{' ' * 5}{_S.RESET}{output_span}",
        f"{_S.DIM}Target band{' ' * 5}{_S.RESET}{cfg.min_freq_hz:.1f} – {cfg.max_freq_hz:.1f} Hz",
        "",
        f"{_S.DIM}{'─' * panel_width}{_S.RESET}",
        f"{_S.DIM}Histogram   {_S.RESET}{hist_line}",
        f"{_S.DIM}Raw notes   {_S.RESET}{raw_line}",
        f"{_S.DIM}Cap rail    {_S.RESET}{cap_line}",
        f"{_S.DIM}Domain      {_S.RESET}{domain_min:.1f} Hz .. {domain_max:.1f} Hz {_S.DIM}(log){_S.RESET}",
        "",
        f"{_S.DIM}{'─' * panel_width}{_S.RESET}",
        f"{_S.DIM}Status      {_S.RESET}{_S.CYAN}{status_message}{_S.RESET}",
        "",
    ]
    return _titled_box(body, panel_width, title="Transpose Studio")


def _transpose_studio_prompt(
    *,
    args: argparse.Namespace,
    cfg: HostConfig,
    midi_path: Path,
    analysis: MidiAnalysisReport,
    compiled: CompileReport,
    avg_active: float,
    key_reader: Callable[[], str] | None = None,
    render_screen: Callable[[list[str]], None] | None = None,
) -> tuple[bool, HostConfig, MidiAnalysisReport, CompileReport, float]:
    if args.yes:
        return True, cfg, analysis, compiled, avg_active

    if key_reader is None and not _supports_transpose_studio():
        return True, cfg, analysis, compiled, avg_active

    base_transpose = analysis.transpose_semitones
    active_transpose = base_transpose
    original_state = (cfg, analysis, compiled, avg_active)
    # Base analysis used as source for fast retranspose previews
    base_analysis = analysis
    # Current preview state — starts fully compiled (not a preview)
    cur_analysis = analysis
    cur_compiled = compiled
    cur_avg_active = avg_active
    is_preview = False
    status_message = f"starting from {_transpose_mode_label(cfg)}"
    if render_screen is None:
        rendered_lines = 0
        clear_line = _S.CLEAR_LINE or "\033[2K"

        def screen(lines: list[str]) -> None:
            nonlocal rendered_lines
            if rendered_lines:
                _out(f"\r\033[{rendered_lines}F", end="")
            total_lines = max(rendered_lines, len(lines))
            for idx in range(total_lines):
                line = lines[idx] if idx < len(lines) else ""
                _out(f"\r{clear_line}{line}")
            rendered_lines = len(lines)

        def finalize_screen() -> None:
            nonlocal rendered_lines
            if rendered_lines:
                _out("\r", end="")
            rendered_lines = 0
    else:
        screen = render_screen

        def finalize_screen() -> None:
            return None

    if key_reader is None:
        fd = sys.stdin.fileno()

        def read_key() -> str:
            return _read_keypress(fd)

        mode: object = _raw_stdin_mode(fd)
    else:
        read_key = key_reader
        mode = nullcontext()

    with mode:
        try:
            while True:
                screen(
                    _build_transpose_studio_lines(
                        cfg=cfg,
                        midi_path=midi_path,
                        analysis=cur_analysis,
                        compiled=cur_compiled,
                        avg_active=cur_avg_active,
                        base_transpose=base_transpose,
                        status_message=status_message,
                        preview=is_preview,
                    )
                )

                key = read_key()
                if key in {"\r", "\n"}:
                    # Full compile for the final transpose if we're in preview
                    if is_preview:
                        next_cfg = replace(cfg, transpose_override=active_transpose, auto_transpose=False)
                        try:
                            cur_analysis, cur_compiled, cur_avg_active, _ = _analyze_and_compile(next_cfg, midi_path)
                            cfg = next_cfg
                        except (RuntimeError, AllocationError, ValueError):
                            return False, *original_state
                    return True, cfg, cur_analysis, cur_compiled, cur_avg_active
                if key == "\x03" or key.lower() in {"q", "x"}:
                    return False, *original_state

                delta = _transpose_delta_from_keypress(key)
                if delta is None:
                    status_message = "Use arrow keys to transpose, Enter to lock in, or q to cancel."
                    continue

                target_transpose = active_transpose + delta
                active_transpose = target_transpose

                if active_transpose == base_transpose:
                    # Back to the original — use fully compiled state
                    cfg, cur_analysis, cur_compiled, cur_avg_active = original_state
                    is_preview = False
                else:
                    # Fast preview: retranspose notes without recompiling
                    cur_analysis = _retranspose_analysis(
                        base_analysis, active_transpose,
                        cfg.min_freq_hz, cfg.max_freq_hz,
                    )
                    is_preview = True
                status_message = f"transpose set to {active_transpose:+d}"
        finally:
            finalize_screen()

# ---------------------------------------------------------------------------
# Live progress display (updates 2 lines in-place)
# ---------------------------------------------------------------------------

_PROGRESS_LINES = 2


class _LiveProgress:
    def __init__(self, total_segments: int, duration_s: float, queue_capacity: int) -> None:
        self._total = total_segments
        self._duration_s = duration_s
        self._queue_cap = queue_capacity
        self._last_render = 0.0
        self._rendered = False

    def update(self, p: StreamProgress) -> None:
        now = time.monotonic()
        if now - self._last_render < 0.25:
            return
        self._last_render = now
        self._draw(p)

    def finish(self, p: StreamProgress) -> None:
        self._draw(p)

    def _draw(self, p: StreamProgress) -> None:
        if self._rendered:
            _out(f"{_S.CURSOR_UP}{_S.CURSOR_UP}", end="")
        self._rendered = True

        frac = p.sent_segments / max(1, self._total)
        playhead_s = p.playhead_us / 1_000_000.0
        time_str = f"{_S.DIM}{_fmt_time(playhead_s)}{_S.RESET} / {_S.BOLD}{_fmt_time(self._duration_s)}{_S.RESET}"

        pct_color = _S.CYAN if frac < 1.0 else _S.GREEN
        line1 = (
            f"{_S.CLEAR_LINE}"
            f"  {_bar(frac)}"
            f"  {pct_color}{_S.BOLD}{frac * 100:5.1f}%{_S.RESET}"
            f"   {time_str}"
        )

        q_color = _S.GREEN if p.queue_depth > 10 else _S.YELLOW
        motor_color = _S.CYAN if p.active_motors > 0 else _S.DIM
        line2 = (
            f"{_S.CLEAR_LINE}"
            f"  {_S.DIM}queue {_S.RESET}{q_color}{p.queue_depth}{_S.RESET}{_S.DIM}/{self._queue_cap}"
            f"    ·    "
            f"{_S.RESET}{motor_color}{p.active_motors}{_S.RESET}{_S.DIM} motors active{_S.RESET}"
        )

        _out(line1)
        _out(line2)


# ---------------------------------------------------------------------------
# Config / CLI plumbing  (unchanged logic)
# ---------------------------------------------------------------------------

def _coerce_idle_mode(value: str) -> IdleMode:
    if value not in {"idle", "duplicate"}:
        raise ValueError(f"invalid idle mode: {value}")
    return value  # type: ignore[return-value]


def _coerce_overflow_mode(value: str) -> OverflowMode:
    if value not in {"steal_quietest", "drop_newest", "strict"}:
        raise ValueError(f"invalid overflow mode: {value}")
    return value  # type: ignore[return-value]


def _coerce_lookahead_strategy(value: str) -> LookaheadStrategy:
    if value not in {"average", "p90", "p95", "percentile"}:
        raise ValueError(f"invalid lookahead strategy: {value}")
    return value  # type: ignore[return-value]


def _coerce_ui_theme(value: str) -> ThemeId:
    return coerce_theme_id(value)


_PIPELINE_PROFILES: dict[str, dict[str, object]] = {
    "clean": {
        "idle_mode": "idle",
        "lookahead_strategy": "p95",
        "lookahead_ms": 1200,
        "lookahead_min_ms": 400,
        "lookahead_min_segments": 24,
    },
    "safe": {
        "idle_mode": "idle",
        "lookahead_strategy": "p95",
        "lookahead_ms": 1500,
        "lookahead_min_ms": 600,
        "lookahead_min_segments": 32,
    },
    "expressive": {
        "idle_mode": "idle",
        "lookahead_strategy": "p90",
        "lookahead_ms": 1000,
        "lookahead_min_ms": 250,
        "lookahead_min_segments": 20,
    },
    "quiet-hw": {
        "idle_mode": "idle",
        "lookahead_strategy": "p95",
        "lookahead_ms": 1400,
        "lookahead_min_ms": 500,
        "lookahead_min_segments": 28,
        "max_freq": 800.0,
    },
}


def _build_config(args: argparse.Namespace) -> HostConfig:
    loaded = load_config(args.config)
    profile_name = getattr(args, "profile", None)
    profile_overrides = _PIPELINE_PROFILES.get(profile_name, {})

    def _pick(name: str, loaded_value: object) -> object:
        cli_value = getattr(args, name, None)
        if cli_value is not None:
            return cli_value
        if name in profile_overrides:
            return profile_overrides[name]
        return loaded_value

    instrument_profile_cli = getattr(args, "instrument_profile", None)
    instrument_profile_path = (
        str(resolve_instrument_profile_path(instrument_profile_cli))
        if instrument_profile_cli
        else loaded.instrument_profile_path
    )

    return HostConfig(
        port=str(_pick("port", loaded.port)),
        baudrate=int(_pick("baud", loaded.baudrate)),
        timeout_s=getattr(args, "timeout", None) if getattr(args, "timeout", None) is not None else loaded.timeout_s,
        write_timeout_s=(
            getattr(args, "write_timeout", None)
            if getattr(args, "write_timeout", None) is not None
            else loaded.write_timeout_s
        ),
        retries=getattr(args, "retries", None) if getattr(args, "retries", None) is not None else loaded.retries,
        connected_motors=int(_pick("motors", loaded.connected_motors)),
        instrument_profile_path=instrument_profile_path,
        idle_mode=_coerce_idle_mode(str(_pick("idle_mode", loaded.idle_mode))),
        overflow_mode=_coerce_overflow_mode(str(_pick("overflow_mode", loaded.overflow_mode))),
        min_freq_hz=float(_pick("min_freq", loaded.min_freq_hz)),
        max_freq_hz=float(_pick("max_freq", loaded.max_freq_hz)),
        auto_transpose=(not getattr(args, "no_auto_transpose", False))
        if getattr(args, "no_auto_transpose", False)
        else loaded.auto_transpose,
        auto_home=loaded.auto_home,
        transpose_override=getattr(args, "transpose", None)
        if getattr(args, "transpose", None) is not None
        else loaded.transpose_override,
        sticky_gap_ms=int(_pick("sticky_gap_ms", loaded.sticky_gap_ms)),
        lookahead_ms=int(_pick("lookahead_ms", loaded.lookahead_ms)),
        lookahead_strategy=_coerce_lookahead_strategy(str(_pick("lookahead_strategy", loaded.lookahead_strategy))),
        lookahead_min_ms=int(_pick("lookahead_min_ms", loaded.lookahead_min_ms)),
        lookahead_percentile=int(_pick("lookahead_percentile", loaded.lookahead_percentile)),
        lookahead_min_segments=int(_pick("lookahead_min_segments", loaded.lookahead_min_segments)),
        home_steps_per_rev=getattr(args, "home_steps_per_rev", None)
        if getattr(args, "home_steps_per_rev", None) is not None
        else loaded.home_steps_per_rev,
        home_hz=getattr(args, "home_hz", None)
        if getattr(args, "home_hz", None) is not None
        else loaded.home_hz,
        home_start_hz=getattr(args, "home_start_hz", None)
        if getattr(args, "home_start_hz", None) is not None
        else loaded.home_start_hz,
        home_accel_hz_per_s=getattr(args, "home_accel_hz_per_s", None)
        if getattr(args, "home_accel_hz_per_s", None) is not None
        else loaded.home_accel_hz_per_s,
        pre_song_warmups=loaded.pre_song_warmups,
        warmup_motor_order=loaded.warmup_motor_order,
        warmup_speed_multipliers=loaded.warmup_speed_multipliers,
        warmup_max_accel_hz_per_s=loaded.warmup_max_accel_hz_per_s,
        warmup_require_home_before_sequence=loaded.warmup_require_home_before_sequence,
        startup_countdown_s=loaded.startup_countdown_s,
        flip_direction_on_note_change=loaded.flip_direction_on_note_change,
        suppress_tight_direction_flips=loaded.suppress_tight_direction_flips,
        direction_flip_safety_margin_ms=loaded.direction_flip_safety_margin_ms,
        direction_flip_cooldown_ms=loaded.direction_flip_cooldown_ms,
        playback_run_accel_hz_per_s=float(
            _pick("playback_run_accel_hz_per_s", loaded.playback_run_accel_hz_per_s)
        ),
        playback_launch_start_hz=float(
            _pick("playback_launch_start_hz", loaded.playback_launch_start_hz)
        ),
        playback_launch_accel_hz_per_s=float(
            _pick("playback_launch_accel_hz_per_s", loaded.playback_launch_accel_hz_per_s)
        ),
        playback_launch_crossover_hz=float(
            _pick("playback_launch_crossover_hz", loaded.playback_launch_crossover_hz)
        ),
        scheduled_start_guard_ms=loaded.scheduled_start_guard_ms,
        ui_host=getattr(args, "ui_host", None) or loaded.ui_host,
        ui_port=getattr(args, "ui_port", None) if getattr(args, "ui_port", None) is not None else loaded.ui_port,
        ui_static_dir=getattr(args, "ui_static_dir", None) or loaded.ui_static_dir,
        ui_theme=_coerce_ui_theme(getattr(args, "ui_theme")) if getattr(args, "ui_theme", None) else loaded.ui_theme,
        ui_color_mode=loaded.ui_color_mode,
        ui_color_modes=loaded.ui_color_modes,
        ui_show_controls=loaded.ui_show_controls,
        ui_sync_offset_ms=loaded.ui_sync_offset_ms,
        verbose=loaded.verbose,
    )


def _speech_config_value(args: argparse.Namespace, name: str, loaded_value: object) -> object:
    cli_value = getattr(args, name, None)
    return cli_value if cli_value is not None else loaded_value


def _build_speech_host_config(args: argparse.Namespace) -> HostConfig:
    loaded = load_config(getattr(args, "config", "config.toml"))
    return replace(
        loaded,
        port=str(_speech_config_value(args, "port", loaded.port)),
        baudrate=int(_speech_config_value(args, "baud", loaded.baudrate)),
        timeout_s=float(_speech_config_value(args, "timeout", loaded.timeout_s)),
        write_timeout_s=float(_speech_config_value(args, "write_timeout", loaded.write_timeout_s)),
        retries=int(_speech_config_value(args, "retries", loaded.retries)),
        startup_countdown_s=int(_speech_config_value(args, "startup_countdown_s", loaded.startup_countdown_s)),
        home_steps_per_rev=int(_speech_config_value(args, "home_steps_per_rev", loaded.home_steps_per_rev)),
        home_hz=float(_speech_config_value(args, "home_hz", loaded.home_hz)),
        home_start_hz=float(_speech_config_value(args, "home_start_hz", loaded.home_start_hz)),
        home_accel_hz_per_s=float(_speech_config_value(args, "home_accel_hz_per_s", loaded.home_accel_hz_per_s)),
    )


def _speech_text_from_args(args: argparse.Namespace) -> str:
    text = getattr(args, "text", None)
    text_file = getattr(args, "text_file", None)
    if bool(text) == bool(text_file):
        raise RuntimeError("provide exactly one of --text or --text-file")
    if text:
        return str(text)
    path = Path(str(text_file))
    if not path.exists():
        raise RuntimeError(f"text file not found: {path}")
    return path.read_text(encoding="utf-8")


def _slugify_text(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:48] or "speech"


def _default_speech_out_path(text: str, *, suffix: str = ".speech.wav") -> Path:
    out_dir = Path(".cache") / "speech_preview"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{_slugify_text(text)}{suffix}"


def _build_speech_playback(args: argparse.Namespace):
    speech_cfg = load_speech_config(getattr(args, "speech_config", "config.speech.toml"))
    backend = getattr(args, "backend", None) or speech_cfg.default_backend
    voice = getattr(args, "voice", None) or speech_cfg.default_voice
    preset_id = getattr(args, "preset", None) or speech_cfg.default_preset
    engine = getattr(args, "engine", None) or speech_cfg.default_engine
    preset = load_speech_preset(preset_id)
    phonemes_file = getattr(args, "phonemes_file", None)
    if phonemes_file:
        utterance = utterance_from_phonemes_file(phonemes_file, voice=voice)
    else:
        utterance = utterance_from_text(
            _speech_text_from_args(args),
            voice=voice,
            backend=backend,
            word_gap_ms=preset.word_gap_ms,
            pause_ms=preset.pause_ms,
        )
    playback = compile_utterance(utterance, preset=preset, engine=engine)
    return speech_cfg, preset, playback


def _speech_json_payload(
    playback,
    *,
    preset_id: str,
    render_result=None,
    evaluation=None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "text": playback.utterance.source_text,
        "normalized_text": playback.utterance.normalized_text,
        "voice": playback.utterance.voice,
        "backend": playback.utterance.backend,
        "engine": playback.report.engine_id,
        "preset": preset_id,
        "mode_id": playback.playback_program.mode_id,
        "compile": {
            "engine_id": playback.report.engine_id,
            "duration_s": playback.report.duration_s,
            "phoneme_count": len(playback.utterance.phonemes),
            "frame_count": playback.report.frame_count,
            "target_count": playback.report.target_count,
            "event_group_count": playback.report.event_group_count,
            "segment_count": playback.report.segment_count,
            "lane_active_ratio": list(playback.report.lane_active_ratio),
            "lane_retarget_count": list(playback.report.lane_retarget_count),
            "burst_count": playback.report.burst_count,
            "max_event_rate_hz": playback.report.max_event_rate_hz,
            "warnings": list(playback.report.warnings),
        },
        "phonemes": [
            {
                "symbol": phoneme.symbol,
                "start_s": phoneme.start_s,
                "duration_s": phoneme.duration_s,
                "stress": phoneme.stress,
            }
            for phoneme in playback.utterance.phonemes
        ],
    }
    if render_result is not None:
        payload["render"] = {
            "wav_path": str(render_result.wav_path),
            "metadata_path": str(render_result.metadata_path),
            "duration_s": render_result.duration_s,
            "sample_rate": render_result.sample_rate,
            "peak": render_result.peak,
            "rms": render_result.rms,
        }
    if evaluation is not None:
        payload["evaluation"] = {
            "recognizer": evaluation.recognizer,
            "available": evaluation.available,
            "recognized_text": evaluation.recognized_text,
            "word_accuracy": evaluation.word_accuracy,
            "word_error_count": evaluation.word_error_count,
            "notes": list(evaluation.notes),
        }
    return payload


def _emit_speech_panel(
    title: str,
    *,
    playback,
    preset_id: str,
    render_result=None,
    evaluation=None,
) -> None:
    panel = _Panel(title=title)
    panel.section("Input")
    panel.row("Text", playback.utterance.source_text)
    panel.row("Voice", playback.utterance.voice)
    panel.row("Backend", playback.utterance.backend)
    panel.row("Engine", playback.report.engine_id)
    panel.row("Preset", preset_id)
    panel.section("Compile")
    panel.row("Phonemes", f"{len(playback.utterance.phonemes):,}")
    panel.row("Frames", f"{playback.report.frame_count:,}")
    panel.row("Event groups", f"{playback.report.event_group_count:,}")
    panel.row("Duration", _fmt_time(playback.report.duration_s))
    panel.row("Max event Hz", f"{playback.report.max_event_rate_hz:.1f}")
    panel.row(
        "Lane active",
        " · ".join(f"{ratio * 100:.0f}%" for ratio in playback.report.lane_active_ratio),
    )
    if render_result is not None:
        panel.section("Render")
        panel.row("WAV", str(render_result.wav_path))
        panel.row("Metadata", str(render_result.metadata_path))
        panel.row("Peak / RMS", f"{render_result.peak:.3f} / {render_result.rms:.3f}")
    if evaluation is not None:
        panel.section("Evaluation")
        panel.row("Recognizer", evaluation.recognizer)
        panel.row("Recognized", evaluation.recognized_text or "n/a")
        panel.row("Word accuracy", f"{evaluation.word_accuracy * 100:.1f}%")
    if playback.report.warnings:
        panel.section("Warnings")
        for warning in playback.report.warnings[:4]:
            panel.raw(warning)
    panel.blank()
    panel.emit()


def _count_active_motors(freqs: Iterable[float]) -> int:
    return sum(1 for freq in freqs if freq > 0.0)


def _load_selected_instrument_profile(cfg: HostConfig) -> InstrumentProfile:
    profile = load_instrument_profile(cfg.instrument_profile_path)
    if cfg.connected_motors > profile.motor_count:
        raise RuntimeError(
            f"config requests {cfg.connected_motors} connected motors but instrument profile only describes "
            f"{profile.motor_count}"
        )
    return profile


def _arrangement_report_dict(report: ArrangementReport) -> dict[str, int | float]:
    return {
        "considered_note_count": report.considered_note_count,
        "preserved_note_count": report.preserved_note_count,
        "dropped_note_count": report.dropped_note_count,
        "truncated_note_count": report.truncated_note_count,
        "melody_note_count": report.melody_note_count,
        "preserved_melody_note_count": report.preserved_melody_note_count,
        "dropped_melody_note_count": report.dropped_melody_note_count,
        "bass_note_count": report.bass_note_count,
        "preserved_bass_note_count": report.preserved_bass_note_count,
        "dropped_bass_note_count": report.dropped_bass_note_count,
        "inner_note_count": report.inner_note_count,
        "dropped_inner_note_count": report.dropped_inner_note_count,
        "octave_retargeted_note_count": report.octave_retargeted_note_count,
        "coalesced_transition_count": report.coalesced_transition_count,
        "requested_reversal_count": report.requested_reversal_count,
        "applied_reversal_count": report.applied_reversal_count,
        "avoided_reversal_count": report.avoided_reversal_count,
        "tight_reversal_window_count": report.tight_reversal_window_count,
        "motor_preferred_band_violation_count": report.motor_preferred_band_violation_count,
        "motor_resonance_band_hit_count": report.motor_resonance_band_hit_count,
        "motor_avoid_band_hit_count": report.motor_avoid_band_hit_count,
        "motor_comfort_violation_count": report.motor_comfort_violation_count,
        "weighted_musical_loss": report.weighted_musical_loss,
    }


@dataclass(frozen=True)
class _PreparedPlaybackArtifacts:
    instrument_profile: InstrumentProfile
    analysis: MidiAnalysisReport
    compiled: CompileReport
    playback_program: PlaybackProgram
    arrangement_report: ArrangementReport
    avg_active: float
    tempo_map: TempoMap | None


def _prepare_playback_artifacts(
    *,
    cfg: HostConfig,
    midi_path: Path,
    instrument_profile: InstrumentProfile,
    analysis: MidiAnalysisReport | None = None,
    compiled: CompileReport | None = None,
    avg_active: float | None = None,
    tempo_map: TempoMap | None = None,
    progress: bool = False,
) -> _PreparedPlaybackArtifacts:
    if analysis is None or compiled is None or avg_active is None or tempo_map is None:
        analysis, compiled, avg_active, tempo_map = _analyze_and_compile(cfg, midi_path, progress=progress)

    playback_program = build_default_playback_program(
        analysis=analysis,
        compiled=compiled,
    )
    arrangement_report = build_arrangement_report(
        analysis=analysis,
        compiled=compiled,
        instrument_profile=instrument_profile,
    )
    return _PreparedPlaybackArtifacts(
        instrument_profile=instrument_profile,
        analysis=analysis,
        compiled=compiled,
        playback_program=playback_program,
        arrangement_report=arrangement_report,
        avg_active=avg_active,
        tempo_map=tempo_map,
    )


def _build_warmup_params(cfg: HostConfig) -> list[list[WarmupMotorParams]]:
    """Build per-routine per-motor trapezoidal warmup profiles from config."""
    if not cfg.pre_song_warmups:
        return []
    return build_warmup_params(
        cfg.pre_song_warmups,
        connected_motors=cfg.connected_motors,
        steps_per_rev=cfg.home_steps_per_rev,
        motor_order=cfg.warmup_motor_order,
        speed_multipliers=dict(cfg.warmup_speed_multipliers),
        max_accel_hz_per_s=cfg.warmup_max_accel_hz_per_s,
    )


def _build_warmup_step_motion_routines(cfg: HostConfig) -> list[list[StepMotionMotorParams]]:
    """Build exact step-motion warmup routines from warmup config."""
    if not cfg.pre_song_warmups:
        return []
    return build_warmup_step_motion_params(
        cfg.pre_song_warmups,
        connected_motors=cfg.connected_motors,
        steps_per_rev=cfg.home_steps_per_rev,
        motor_order=cfg.warmup_motor_order,
        speed_multipliers=dict(cfg.warmup_speed_multipliers),
        max_accel_hz_per_s=cfg.warmup_max_accel_hz_per_s,
    )


def _supports_home(feature_flags: int) -> bool:
    return (feature_flags & FEATURE_FLAG_HOME) != 0


def _supports_direction_flip(feature_flags: int) -> bool:
    return (feature_flags & FEATURE_FLAG_DIRECTION_FLIP) != 0


def _supports_continuous_playback_engine(feature_flags: int) -> bool:
    return (feature_flags & FEATURE_FLAG_CONTINUOUS_PLAYBACK_ENGINE) != 0


def _supports_playback_setup_profile(feature_flags: int) -> bool:
    return (feature_flags & FEATURE_FLAG_PLAYBACK_SETUP_PROFILE) != 0


def _supports_speech_assist(feature_flags: int) -> bool:
    return (feature_flags & FEATURE_FLAG_SPEECH_ASSIST) != 0


def _supports_playback_event_streaming(protocol_version: int, feature_flags: int) -> bool:
    return protocol_version >= 2 and (feature_flags & FEATURE_FLAG_TIMED_STREAMING) != 0


def _run_auto_home(client: SerialClient, cfg: HostConfig) -> None:
    client.home(
        steps_per_rev=cfg.home_steps_per_rev,
        home_hz=cfg.home_hz,
        start_hz=cfg.home_start_hz,
        accel_hz_per_s=cfg.home_accel_hz_per_s,
    )


def _prompt_play(args: argparse.Namespace) -> bool:
    if args.yes:
        return True
    while True:
        try:
            _out(f"\n  {_S.DIM}{'─' * 40}{_S.RESET}")
            raw = input(
                f"  {_S.GREEN}{_S.BOLD}Ready.{_S.RESET} Press {_S.BOLD}Enter{_S.RESET} to start playback "
                f"{_S.DIM}(or q + Enter to cancel){_S.RESET} "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        if raw == "":
            return True
        if raw in {"q", "quit", "n", "no", "cancel"}:
            return False
        _out(f"  {_S.DIM}press Enter to start, or q to cancel{_S.RESET}")


def _start_playback_countdown(args: argparse.Namespace, *, seconds: int) -> None:
    """Delay interactive playback start to give operators a predictable lead-in."""
    if args.yes or seconds <= 0:
        return
    for remaining in range(seconds, 0, -1):
        noun = "second" if remaining == 1 else "seconds"
        _out(
            f"  {_S.DIM}Starting warmup/music in {_S.BOLD}{remaining}{_S.RESET} "
            f"{_S.DIM}{noun}...{_S.RESET}"
        )
        time.sleep(1.0)
    _out(f"  {_S.DIM}Starting now.{_S.RESET}")


def _compile_progress(current: int, total: int) -> None:
    """Inline progress bar for note allocation."""
    frac = current / total if total else 1.0
    bar = _bar(frac, width=20)
    _out(f"\r  {'Compiling':<16}{bar}  {frac:>4.0%}", end="")
    if current >= total:
        _out()


def _analyze_and_compile(
    cfg: HostConfig,
    midi_path: Path,
    *,
    progress: bool = False,
) -> tuple[MidiAnalysisReport, CompileReport, float, TempoMap]:
    instrument_profile = _load_selected_instrument_profile(cfg)
    analysis, tempo_map = analyze_midi(
        midi_path=midi_path,
        min_freq_hz=cfg.min_freq_hz,
        max_freq_hz=cfg.max_freq_hz,
        transpose_override=cfg.transpose_override,
        auto_transpose=cfg.auto_transpose,
    )

    if progress:
        _out(
            f"\r{_S.CLEAR_LINE}  {_S.DIM}Analyzed {analysis.note_count:,} notes — "
            f"allocating to {cfg.connected_motors} motors…{_S.RESET}"
        )

    compiled = compile_segments(
        analysis.notes,
        CompileOptions(
            connected_motors=cfg.connected_motors,
            idle_mode=cfg.idle_mode,
            overflow_mode=cfg.overflow_mode,
            sticky_gap_s=cfg.sticky_gap_ms / 1000.0,
            melody_doubling_enabled=cfg.double_melody,
            flip_direction_on_note_change=cfg.flip_direction_on_note_change,
            suppress_tight_direction_flips=cfg.suppress_tight_direction_flips,
            direction_flip_safety_margin_ms=cfg.direction_flip_safety_margin_ms,
            direction_flip_cooldown_ms=cfg.direction_flip_cooldown_ms,
            playback_run_accel_hz_per_s=cfg.playback_run_accel_hz_per_s,
            playback_launch_start_hz=cfg.playback_launch_start_hz,
            playback_launch_accel_hz_per_s=cfg.playback_launch_accel_hz_per_s,
            playback_launch_crossover_hz=cfg.playback_launch_crossover_hz,
        ),
        instrument_profile=instrument_profile,
        progress_callback=_compile_progress if progress else None,
    )
    if not compiled.event_groups:
        raise RuntimeError("compiled song is empty")

    avg_active = sum(_count_active_motors(seg.motor_freq_hz) for seg in compiled.segments) / max(1, len(compiled.segments))
    return analysis, compiled, avg_active, tempo_map


def _prerender_progress(current: int, total: int) -> None:
    """Inline progress bar for UI timeline prerendering."""
    frac = current / total if total else 1.0
    bar = _bar(frac, width=20)
    _out(f"\r  {'Prerendering':<16}{bar}  {frac:>4.0%}", end="")
    if current >= total:
        _out()  # final newline


def _start_ui(
    *,
    cfg: HostConfig,
    analysis: MidiAnalysisReport,
    compiled: CompileReport,
    playback_program: PlaybackProgram,
    midi_path: Path,
    queue_capacity: int,
    scheduler_tick_us: int,
    ws_poll_interval_s: float,
    ui_render_mode: str,
) -> tuple[DashboardObserver, str]:
    dashboard = DashboardObserver.start(
        cfg=cfg,
        analysis=analysis,
        compiled=compiled,
        playback_program=playback_program,
        midi_path=midi_path,
        queue_capacity=queue_capacity,
        scheduler_tick_us=scheduler_tick_us,
        ws_poll_interval_s=ws_poll_interval_s,
        ui_render_mode=ui_render_mode,
        prerender_progress=_prerender_progress if ui_render_mode == "prerender-30" else None,
    )
    return dashboard, dashboard.render_mode


# ---------------------------------------------------------------------------
# Main command
# ---------------------------------------------------------------------------

def run_command(args: argparse.Namespace) -> int:
    cfg = _build_config(args)
    instrument_profile = _load_selected_instrument_profile(cfg)

    if cfg.auto_home and cfg.home_hz > 200.0 and not getattr(args, "allow_high_home_hz", False):
        raise RuntimeError(
            f"home_hz={cfg.home_hz:.1f} exceeds safety limit 200.0 Hz; "
            "pass --allow-high-home-hz to override explicitly"
        )
    midi_path = Path(args.midi_path)
    if not midi_path.exists():
        raise RuntimeError(f"MIDI file not found: {midi_path}")

    _out(f"\n  {_S.DIM}Analyzing and compiling…{_S.RESET}", end="")
    _prep_t0 = time.monotonic()
    prepared = _prepare_playback_artifacts(
        cfg=cfg,
        midi_path=midi_path,
        instrument_profile=instrument_profile,
        progress=True,
    )
    _prep_elapsed = time.monotonic() - _prep_t0
    _out(
        f"\r{_S.CLEAR_LINE}  {_S.DIM}Compiled "
        f"{len(prepared.compiled.segments):,} segments from "
        f"{prepared.analysis.note_count:,} notes "
        f"({_prep_elapsed:.1f}s){_S.RESET}"
    )
    initial_transpose = prepared.analysis.transpose_semitones

    _render_run_summary(
        args=args,
        cfg=cfg,
        instrument_profile=instrument_profile,
        arrangement_report=prepared.arrangement_report,
        midi_path=midi_path,
        analysis=prepared.analysis,
        compiled=prepared.compiled,
        avg_active=prepared.avg_active,
    )

    accepted, cfg, analysis, compiled, avg_active = _transpose_studio_prompt(
        args=args,
        cfg=cfg,
        midi_path=midi_path,
        analysis=prepared.analysis,
        compiled=prepared.compiled,
        avg_active=prepared.avg_active,
    )
    if not accepted:
        _out(f"\n  {_S.DIM}Canceled before playback start.{_S.RESET}\n")
        return 0

    prepared = _prepare_playback_artifacts(
        cfg=cfg,
        midi_path=midi_path,
        instrument_profile=instrument_profile,
        analysis=analysis,
        compiled=compiled,
        avg_active=avg_active,
        tempo_map=prepared.tempo_map,
    )

    if prepared.analysis.transpose_semitones != initial_transpose:
        selected = _preflight_stats(prepared.analysis, cfg)
        sp = _Panel(title="Selection")
        sp.blank()
        sp.row("Transpose", f"{_S.BOLD}{prepared.analysis.transpose_semitones:+d} semitones{_S.RESET}")
        sp.row(
            "Output span",
            _fmt_freq_range(
                float(selected["output_min_freq_hz"]) if selected["output_min_freq_hz"] is not None else None,
                float(selected["output_max_freq_hz"]) if selected["output_max_freq_hz"] is not None else None,
            ),
        )
        sp.row(
            "Clamped notes",
            f"{analysis.clamped_note_count:,}/{analysis.note_count:,} ({float(selected['clamped_pct']):.1f}%)",
        )
        sp.blank()
        sp.emit()

    warmup_step_motion_routines = _build_warmup_step_motion_routines(cfg)
    warmup_requires_directional_exact_motion = any(
        phase.direction < 0
        for routine in warmup_step_motion_routines
        for motor in routine
        for phase in motor.phases
    )
    playback_plan = prepared.playback_program.playback_plan
    runner = PlaybackRunner(
        port=cfg.port,
        baudrate=cfg.baudrate,
        timeout_s=cfg.timeout_s,
        write_timeout_s=cfg.write_timeout_s,
        retries=cfg.retries,
        client_cls=SerialClient,
    )

    # -- Connect & play --------------------------------------------------------

    _out(f"\n  {_S.BOLD}{_S.CYAN}Playing{_S.RESET}")
    _out(f"  {_S.DIM}{'─' * 40}{_S.RESET}")

    queue_cap = 0
    metrics: PlaybackMetrics | None = None
    dashboard: DashboardObserver | None = None
    ui_render_mode = getattr(args, "ui_render_mode", "prerender-30")
    ui_high_rate = bool(args.ui_high_rate)
    status_poll_interval_s = 0.01 if ui_high_rate else 0.02
    metrics_poll_interval_s = 0.10 if ui_high_rate else 0.25
    ws_poll_interval_s = 0.01 if ui_high_rate else 0.02
    capabilities = None

    try:
        with runner.session() as session:
            capabilities = session.capabilities
            queue_cap = capabilities.queue_capacity
            session.validate(
                connected_motors=cfg.connected_motors,
                requires_direction_flip=cfg.flip_direction_on_note_change,
            )
            if warmup_step_motion_routines and not capabilities.step_motion_supported:
                raise RuntimeError(
                    "configured warmups require STEP_MOTION exact playback support; update firmware before running"
                )
            if warmup_requires_directional_exact_motion and not capabilities.exact_direction_step_motion_supported:
                raise RuntimeError(
                    "configured warmups require directional exact-motion support; update firmware before running"
                )
            if warmup_step_motion_routines and cfg.warmup_require_home_before_sequence and not capabilities.home_supported:
                raise RuntimeError(
                    "configured warmups require HOME support to guarantee 12:00 alignment before the sequence"
                )
            _row(
                "Device",
                (
                    f"ESP32  {_S.DIM}(protocol v{capabilities.protocol_version}, queue: {queue_cap}, "
                    f"tick: {capabilities.scheduler_tick_us} us, playback motors: {capabilities.playback_motor_count}){_S.RESET}"
                ),
            )
            _row(
                "Playback plan",
                (
                    f"{playback_plan.event_group_count:,} event groups"
                    f"  {_S.DIM}({playback_plan.motor_change_count:,} motor changes){_S.RESET}"
                ),
            )
            _row(
                "Instrument",
                (
                    f"{prepared.instrument_profile.name}"
                    f"  {_S.DIM}({cfg.connected_motors}/{prepared.instrument_profile.motor_count} motors, "
                    f"{Path(cfg.instrument_profile_path).name}){_S.RESET}"
                ),
            )
            _row(
                "Arrangement",
                (
                    f"melody drops {prepared.arrangement_report.dropped_melody_note_count:,}"
                    f"  {_S.DIM}(inner {prepared.arrangement_report.dropped_inner_note_count:,}, "
                    f"loss {prepared.arrangement_report.weighted_musical_loss:.2f}){_S.RESET}"
                ),
            )
            if cfg.flip_direction_on_note_change:
                _row("Dir flip", f"{_S.CYAN}enabled on note changes{_S.RESET}")
                if prepared.compiled.direction_flip_requested_count > 0:
                    _row(
                        "Flip plan",
                        (
                            f"requested {prepared.compiled.direction_flip_requested_count:,}"
                            f"  {_S.DIM}(applied {prepared.compiled.direction_flip_applied_count:,}, "
                            f"suppressed {prepared.compiled.direction_flip_suppressed_count:,}, "
                            f"cooldown {prepared.compiled.direction_flip_cooldown_suppressed_count:,})"
                            f"{_S.RESET}"
                        ),
                    )
            _row(
                "Playback tune",
                (
                    f"launch {cfg.playback_launch_start_hz:.1f}->{cfg.playback_launch_crossover_hz:.1f} Hz"
                    f"  {_S.DIM}(launch accel {cfg.playback_launch_accel_hz_per_s:.1f} Hz/s, "
                    f"run accel {cfg.playback_run_accel_hz_per_s:.1f} Hz/s, "
                    f"flip cooldown {cfg.direction_flip_cooldown_ms:.0f} ms, "
                    f"margin {cfg.direction_flip_safety_margin_ms:.0f} ms){_S.RESET}"
                ),
            )
            if prepared.compiled.tight_boundary_warning_count > 0:
                _row(
                    "Tight windows",
                    (
                        f"{_S.YELLOW}{prepared.compiled.tight_boundary_warning_count:,} event boundaries"
                        f"{_S.RESET}"
                    ),
                )
            if prepared.compiled.truncated_note_count > 0 or prepared.compiled.dropped_note_count > 0:
                _row(
                    "Overflow",
                    (
                        f"{_S.YELLOW}{prepared.compiled.truncated_note_count:,} truncated"
                        f"  {_S.DIM}({prepared.compiled.zero_length_note_count:,} zero, "
                        f"steals {prepared.compiled.stolen_note_count:,}, drops {prepared.compiled.dropped_note_count:,})"
                        f"{_S.RESET}"
                    ),
                )
            if capabilities.home_supported and cfg.auto_home:
                _row(
                    "Auto-home",
                    (
                        f"enabled  {_S.DIM}({cfg.home_steps_per_rev} steps/rev, "
                        f"start {cfg.home_start_hz:.1f} Hz -> {cfg.home_hz:.1f} Hz, "
                        f"accel {cfg.home_accel_hz_per_s:.1f} Hz/s){_S.RESET}"
                    ),
                )
            elif capabilities.home_supported and not cfg.auto_home:
                _row("Auto-home", f"{_S.DIM}disabled in config{_S.RESET}")
            else:
                _row("Auto-home", f"{_S.YELLOW}unsupported firmware; will skip{_S.RESET}")
            if warmup_step_motion_routines:
                _row(
                    "Warmups",
                    f"{', '.join(cfg.pre_song_warmups)}  {_S.DIM}(exact step-motion){_S.RESET}",
                )
                _row("Warmup accel", f"{cfg.warmup_max_accel_hz_per_s:.1f} Hz/s")
                if cfg.warmup_require_home_before_sequence:
                    _row("Warmup home", f"{_S.CYAN}required before sequence{_S.RESET}")
                if warmup_requires_directional_exact_motion:
                    _row("Warmup dir", f"{_S.CYAN}directional exact phases enabled{_S.RESET}")
                if cfg.warmup_motor_order:
                    _row("Warmup order", " -> ".join(str(idx) for idx in cfg.warmup_motor_order))
                active_speed_factors = [
                    f"{warmup}×{factor:.2f}"
                    for warmup, factor in cfg.warmup_speed_multipliers
                    if warmup in cfg.pre_song_warmups and abs(float(factor) - 1.0) > 1e-9
                ]
                if active_speed_factors:
                    _row("Warmup speed", ", ".join(active_speed_factors))

            if args.ui:
                dashboard, session_render_mode = _start_ui(
                    cfg=cfg,
                    analysis=prepared.analysis,
                    compiled=prepared.compiled,
                    playback_program=prepared.playback_program,
                    midi_path=midi_path,
                    queue_capacity=queue_cap,
                    scheduler_tick_us=capabilities.scheduler_tick_us,
                    ws_poll_interval_s=ws_poll_interval_s,
                    ui_render_mode=ui_render_mode,
                )
                _row("Dashboard", dashboard.origin)
                if ui_high_rate:
                    _row("UI mode", "high-rate (100 Hz status / 100 Hz WS)")
                else:
                    _row("UI mode", session_render_mode)
            _out()

            session.setup(
                motors=cfg.connected_motors,
                idle_mode=cfg.idle_mode,
                min_note=max(0, min(127, int(round(prepared.analysis.min_source_note or 0)))),
                max_note=max(0, min(127, int(round(prepared.analysis.max_source_note or 127)))),
                transpose=prepared.analysis.transpose_semitones,
                playback_run_accel_hz_per_s=cfg.playback_run_accel_hz_per_s,
                playback_launch_start_hz=cfg.playback_launch_start_hz,
                playback_launch_accel_hz_per_s=cfg.playback_launch_accel_hz_per_s,
                playback_launch_crossover_hz=cfg.playback_launch_crossover_hz,
            )

            if not _prompt_play(args):
                _out(f"\n  {_S.DIM}Canceled before playback start.{_S.RESET}\n")
                return 0

            progress = _LiveProgress(
                total_segments=playback_plan.event_group_count,
                duration_s=prepared.analysis.duration_s,
                queue_capacity=queue_cap,
            )
            progress_observer = CallbackPlaybackObserver(
                on_progress_cb=progress.update,
                on_complete_cb=lambda latest_metrics, last_progress: progress.finish(last_progress)
                if last_progress is not None
                else None,
            )
            observer = CompositePlaybackObserver(progress_observer, dashboard)

            if dashboard is not None and dashboard.render_mode == "live":
                initial_status = session.client.status()
                try:
                    initial_metrics = session.client.metrics()
                except SerialClientError:
                    initial_metrics = None
                dashboard.prime(
                    status=initial_status,
                    metrics=initial_metrics,
                    total_segments=playback_plan.event_group_count,
                )

            result = session.execute_plan(
                playback_plan=playback_plan,
                lookahead_ms=cfg.lookahead_ms,
                lookahead_strategy=cfg.lookahead_strategy,
                lookahead_min_ms=cfg.lookahead_min_ms,
                lookahead_percentile=cfg.lookahead_percentile,
                lookahead_min_segments=cfg.lookahead_min_segments,
                metrics_poll_interval_s=metrics_poll_interval_s,
                status_poll_interval_s=status_poll_interval_s,
                scheduled_start_guard_ms=cfg.scheduled_start_guard_ms,
                clock_sync_samples=8,
                startup_countdown_s=cfg.startup_countdown_s,
                run_countdown=lambda seconds: _start_playback_countdown(args, seconds=seconds),
                auto_home_enabled=cfg.auto_home,
                run_auto_home=lambda client: _run_auto_home(client, cfg),
                warmup_step_motion_routines=warmup_step_motion_routines,
                warmup_require_home_before_sequence=cfg.warmup_require_home_before_sequence,
                warmup_requires_directional_exact_motion=warmup_requires_directional_exact_motion,
                observer=observer,
            )

            if result is None:
                raise RuntimeError("playback did not produce a result")

            metrics = result.metrics
            if capabilities.home_supported and cfg.auto_home and result.auto_home_skipped_reason is not None:
                _row("Auto-home", f"{_S.YELLOW}skipped: {result.auto_home_skipped_reason}{_S.RESET}")
            elif capabilities.home_supported and cfg.auto_home and result.auto_home_error is not None:
                _row("Auto-home", f"{_S.YELLOW}failed: {result.auto_home_error}{_S.RESET}")
            elif capabilities.home_supported and cfg.auto_home:
                _row("Auto-home", f"{_S.GREEN}complete{_S.RESET}")
            elif capabilities.home_supported and not cfg.auto_home:
                _row("Auto-home", f"{_S.DIM}disabled in config{_S.RESET}")
            else:
                _row("Auto-home", f"{_S.YELLOW}skipped (firmware lacks HOME capability){_S.RESET}")

            if dashboard is not None and dashboard.render_mode == "live" and result.last_progress is not None:
                final_status = session.client.status()
                dashboard.publish_final(
                    status=final_status,
                    metrics=metrics,
                    last_progress=result.last_progress,
                )
    finally:
        if dashboard is not None:
            dashboard.close()

    if metrics is None:
        raise RuntimeError("failed to read playback metrics")

    # -- Results ---------------------------------------------------------------

    underruns = metrics.underrun_count
    late_us = metrics.scheduling_late_max_us
    hwm = metrics.queue_high_water
    crc_errs = metrics.crc_parse_errors
    rx_errs = metrics.rx_parse_errors
    scheduler_guard_hits = metrics.scheduler_guard_hits
    event_groups_started = metrics.event_groups_started
    control_late_us = metrics.control_late_max_us
    control_overruns = metrics.control_overrun_count
    wave_period_updates = metrics.wave_period_update_count
    motor_start_count = metrics.motor_start_count
    motor_stop_count = metrics.motor_stop_count
    flip_restarts = metrics.flip_restart_count
    launch_guards = metrics.launch_guard_count
    engine_fault_count = metrics.engine_fault_count
    engine_fault_mask = metrics.engine_fault_mask
    exact_position_lost_mask = metrics.exact_position_lost_mask
    playback_position_unreliable_mask = metrics.playback_position_unreliable_mask
    event_group_mismatch = event_groups_started != playback_plan.event_group_count

    clean = (
        underruns == 0
        and crc_errs == 0
        and rx_errs == 0
        and scheduler_guard_hits == 0
        and control_overruns == 0
        and launch_guards == 0
        and engine_fault_count == 0
        and engine_fault_mask == 0
        and exact_position_lost_mask == 0
        and playback_position_unreliable_mask == 0
        and not event_group_mismatch
    )

    rp = _Panel(title="Results")
    rp.blank()
    if clean:
        rp.raw(f"{_S.GREEN}{_S.BOLD}Complete{_S.RESET}")
    else:
        rp.raw(f"{_S.YELLOW}{_S.BOLD}Complete (with warnings){_S.RESET}")
    rp.blank()

    u_color = _S.GREEN if underruns == 0 else _S.RED
    rp.row("Underruns", f"{u_color}{underruns}{_S.RESET}")

    l_color = _S.GREEN if late_us < 1000 else (_S.YELLOW if late_us < 5000 else _S.RED)
    rp.row("Max late", f"{l_color}{late_us:,} us{_S.RESET}")

    control_late_color = _S.GREEN if control_late_us < 100 else (_S.YELLOW if control_late_us < 1000 else _S.RED)
    rp.row("Control late", f"{control_late_color}{control_late_us:,} us{_S.RESET}")

    rp.row("Peak queue", f"{hwm}/{queue_cap}")
    event_group_color = _S.GREEN if not event_group_mismatch else _S.RED
    rp.row(
        "Event groups",
        f"{event_group_color}{event_groups_started:,}/{playback_plan.event_group_count:,} started{_S.RESET}",
    )
    rp.row("Motor changes", f"{playback_plan.motor_change_count:,}")

    if crc_errs > 0:
        rp.row("CRC errors", f"{_S.RED}{crc_errs}{_S.RESET}")
    if rx_errs > 0:
        rp.row("RX parse errors", f"{_S.RED}{rx_errs}{_S.RESET}")
    if metrics.timer_empty_events > 0:
        rp.row("Timer empty", f"{_S.YELLOW}{metrics.timer_empty_events}{_S.RESET}")
    if metrics.timer_restart_count > 0:
        rp.row("Timer restarts", f"{_S.DIM}{metrics.timer_restart_count}{_S.RESET}")
    if scheduler_guard_hits > 0:
        rp.row("Sched guards", f"{_S.YELLOW}{scheduler_guard_hits}{_S.RESET}")
    if control_overruns > 0:
        rp.row("Control overruns", f"{_S.RED}{control_overruns}{_S.RESET}")
    if wave_period_updates > 0:
        rp.row("Wave updates", f"{_S.DIM}{wave_period_updates:,}{_S.RESET}")
    if motor_start_count > 0 or motor_stop_count > 0:
        rp.row(
            "Motor state",
            f"{_S.DIM}{motor_start_count:,} starts / {motor_stop_count:,} stops{_S.RESET}",
        )
    if flip_restarts > 0:
        rp.row(
            "Flip restarts",
            f"{_S.DIM}{flip_restarts:,}{_S.RESET}",
        )
    if launch_guards > 0:
        rp.row("Launch guards", f"{_S.YELLOW}{launch_guards:,}{_S.RESET}")
    if engine_fault_count > 0:
        rp.row("Engine faults", f"{_S.RED}{engine_fault_count:,}{_S.RESET}")
    if engine_fault_mask > 0:
        rp.row("Engine mask", f"{_S.RED}0x{engine_fault_mask:02X}{_S.RESET}")
    if exact_position_lost_mask > 0:
        rp.row("Exact pos lost", f"{_S.RED}0x{exact_position_lost_mask:02X}{_S.RESET}")
    if playback_position_unreliable_mask > 0:
        rp.row("Playback pos", f"{_S.RED}unreliable 0x{playback_position_unreliable_mask:02X}{_S.RESET}")
    if metrics.inferred_pulse_total > 0 or metrics.measured_pulse_total > 0:
        rp.row(
            "Pulse counts",
            (
                f"{_S.DIM}inferred {metrics.inferred_pulse_total:,}, "
                f"measured {metrics.measured_pulse_total:,}{_S.RESET}"
            ),
        )
    if metrics.measured_pulse_drift_total > 0:
        rp.row("Pulse drift", f"{_S.YELLOW}{metrics.measured_pulse_drift_total:,}{_S.RESET}")
    if metrics.playback_signed_position_drift_total > 0:
        rp.row("Signed drift", f"{_S.YELLOW}{metrics.playback_signed_position_drift_total:,}{_S.RESET}")
    rp.blank()

    rp.emit()

    return 0


def ui_preview_command(args: argparse.Namespace) -> int:
    cfg = _build_config(args)
    instrument_profile = _load_selected_instrument_profile(cfg)
    midi_path = Path(args.midi_path)
    if not midi_path.exists():
        raise RuntimeError(f"MIDI file not found: {midi_path}")

    prepared = _prepare_playback_artifacts(
        cfg=cfg,
        midi_path=midi_path,
        instrument_profile=instrument_profile,
    )

    p = _Panel(title="music2 · ui-preview")
    p.section("Song")
    p.row("File", midi_path.name)
    p.row("Duration", _fmt_time(prepared.analysis.duration_s))
    p.row("Notes", f"{prepared.analysis.note_count:,}  {_S.DIM}(polyphony: {prepared.analysis.max_polyphony}){_S.RESET}")
    p.row(
        "Instrument",
        f"{instrument_profile.name}  {_S.DIM}({cfg.connected_motors}/{instrument_profile.motor_count} motors){_S.RESET}",
    )
    p.row(
        "Event groups",
        f"{prepared.playback_program.playback_plan.event_group_count:,}  {_S.DIM}(avg {prepared.avg_active:.1f} active){_S.RESET}",
    )
    p.row("Motor changes", f"{prepared.playback_program.playback_plan.motor_change_count:,}")
    p.row(
        "Arrangement",
        (
            f"melody drops {prepared.arrangement_report.dropped_melody_note_count:,}/{prepared.arrangement_report.melody_note_count:,}"
            f"  {_S.DIM}(comfort hits {prepared.arrangement_report.motor_comfort_violation_count:,}){_S.RESET}"
        ),
    )
    p.row(
        "Allocation",
        (
            f"retained {prepared.analysis.note_count - prepared.compiled.dropped_note_count:,}/{prepared.analysis.note_count:,}"
            f"  {_S.DIM}(steals {prepared.compiled.stolen_note_count:,}, drops {prepared.compiled.dropped_note_count:,}){_S.RESET}"
        ),
    )
    if prepared.compiled.direction_flip_requested_count > 0:
        p.row(
            "Dir flips",
            (
                f"requested {prepared.compiled.direction_flip_requested_count:,}"
                f"  {_S.DIM}(applied {prepared.compiled.direction_flip_applied_count:,}, "
                f"suppressed {prepared.compiled.direction_flip_suppressed_count:,}, "
                f"cooldown {prepared.compiled.direction_flip_cooldown_suppressed_count:,}){_S.RESET}"
            ),
        )
    if prepared.compiled.truncated_note_count > 0 or prepared.compiled.dropped_note_count > 0:
        p.row(
            "Overflow risk",
            (
                f"{_S.YELLOW}{prepared.compiled.truncated_note_count:,} truncated"
                f"  {_S.DIM}({prepared.compiled.zero_length_note_count:,} zero){_S.RESET}"
            ),
        )
    if prepared.compiled.tight_boundary_warning_count > 0:
        p.row(
            "Tight windows",
            f"{prepared.compiled.tight_boundary_warning_count:,}  {_S.DIM}event boundaries{_S.RESET}",
        )

    dashboard = None
    try:
        dashboard, session_render_mode = _start_ui(
            cfg=cfg,
            analysis=prepared.analysis,
            compiled=prepared.compiled,
            playback_program=prepared.playback_program,
            midi_path=midi_path,
            queue_capacity=128,
            scheduler_tick_us=10,
            ws_poll_interval_s=0.02,
            ui_render_mode=args.ui_render_mode,
        )
        p.section("Viewer")
        p.row("Dashboard", dashboard.origin)
        p.row("Mode", session_render_mode)
        p.blank()
        p.emit()
        _out(f"  {_S.DIM}Press Ctrl-C to stop.{_S.RESET}\n")
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        return 0
    finally:
        if dashboard is not None:
            dashboard.close()


def _percentile(sorted_values: list[int], pct: int) -> int:
    if not sorted_values:
        return 0
    if len(sorted_values) == 1:
        return sorted_values[0]
    clamped_pct = max(0, min(100, pct))
    idx = (len(sorted_values) - 1) * (clamped_pct / 100.0)
    low = int(idx)
    high = min(len(sorted_values) - 1, low + 1)
    alpha = idx - low
    return int(round((1.0 - alpha) * sorted_values[low] + alpha * sorted_values[high]))


def _segment_short_counts(durations_us: list[int]) -> dict[str, int]:
    return {
        "<=200us": sum(1 for value in durations_us if value <= 200),
        "<=500us": sum(1 for value in durations_us if value <= 500),
        "<=1ms": sum(1 for value in durations_us if value <= 1_000),
        "<=2ms": sum(1 for value in durations_us if value <= 2_000),
        "<=5ms": sum(1 for value in durations_us if value <= 5_000),
    }


def analyze_command(args: argparse.Namespace) -> int:
    cfg = _build_config(args)
    instrument_profile = _load_selected_instrument_profile(cfg)
    midi_path = Path(args.midi_path)
    if not midi_path.exists():
        raise RuntimeError(f"MIDI file not found: {midi_path}")

    prepared = _prepare_playback_artifacts(
        cfg=cfg,
        midi_path=midi_path,
        instrument_profile=instrument_profile,
    )
    playback_plan = prepared.playback_program.playback_plan
    durations_us = [max(1, group.delta_us) for group in playback_plan.event_groups]
    sorted_durations = sorted(durations_us)
    total_us = sum(durations_us)
    expected_us = int(round(prepared.analysis.duration_s * 1_000_000.0))
    duration_delta_us = total_us - expected_us
    short_counts = _segment_short_counts(durations_us)
    summary = {
        "file": str(midi_path),
        "instrument": {
            "profile_name": instrument_profile.name,
            "profile_version": instrument_profile.profile_version,
            "profile_path": str(instrument_profile.source_path or cfg.instrument_profile_path),
            "motor_count": instrument_profile.motor_count,
            "active_motor_count": cfg.connected_motors,
            "motor_labels": [motor.label for motor in instrument_profile.ordered_motors[: cfg.connected_motors]],
        },
        "arrangement": _arrangement_report_dict(prepared.arrangement_report),
        "notes": {
            "count": prepared.analysis.note_count,
            "max_polyphony": prepared.analysis.max_polyphony,
            "transpose_semitones": prepared.analysis.transpose_semitones,
            "clamped_note_count": prepared.analysis.clamped_note_count,
        },
        "allocation": {
            "policy": prepared.compiled.overflow_mode,
            "connected_motors": prepared.compiled.connected_motors,
            "stolen_note_count": prepared.compiled.stolen_note_count,
            "dropped_note_count": prepared.compiled.dropped_note_count,
            "truncated_note_count": prepared.compiled.truncated_note_count,
            "zero_length_note_count": prepared.compiled.zero_length_note_count,
            "retained_note_count": prepared.analysis.note_count - prepared.compiled.dropped_note_count,
            "adjacent_segments_merged": prepared.compiled.adjacent_segments_merged,
            "short_segments_absorbed": prepared.compiled.short_segments_absorbed,
            "direction_flip_requested_count": prepared.compiled.direction_flip_requested_count,
            "direction_flip_applied_count": prepared.compiled.direction_flip_applied_count,
            "direction_flip_suppressed_count": prepared.compiled.direction_flip_suppressed_count,
            "direction_flip_cooldown_suppressed_count": prepared.compiled.direction_flip_cooldown_suppressed_count,
            "tight_boundary_warning_count": prepared.compiled.tight_boundary_warning_count,
        },
        "playback_plan": {
            "event_group_count": playback_plan.event_group_count,
            "motor_change_count": playback_plan.motor_change_count,
            "duration_total_us": total_us,
            "duration_expected_us": expected_us,
            "duration_delta_us": duration_delta_us,
            "min_delta_us": min(sorted_durations) if sorted_durations else 0,
            "median_delta_us": int(statistics.median(sorted_durations)) if sorted_durations else 0,
            "p90_delta_us": _percentile(sorted_durations, 90),
            "p95_delta_us": _percentile(sorted_durations, 95),
            "max_delta_us": max(sorted_durations) if sorted_durations else 0,
            "short_delta_counts": short_counts,
            "avg_active_motors": prepared.avg_active,
        },
        "shadow_segments": {
            "count": len(durations_us),
            "count_from_projection": playback_plan.shadow_segment_count,
        },
        "playback_program": {
            "mode_id": prepared.playback_program.mode_id,
            "display_name": prepared.playback_program.display_name,
            "section_count": len(prepared.playback_program.sections),
            "duration_total_us": prepared.playback_program.total_duration_us,
        },
        "lookahead": {
            "lookahead_ms": cfg.lookahead_ms,
            "lookahead_strategy": cfg.lookahead_strategy,
            "lookahead_min_ms": cfg.lookahead_min_ms,
            "lookahead_percentile": cfg.lookahead_percentile,
            "lookahead_min_segments": cfg.lookahead_min_segments,
        },
    }

    if args.json:
        _out(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    ap = _Panel(title="music2 · analyze")
    ap.section("Song")
    ap.row("File", midi_path.name)
    ap.row("Notes", f"{prepared.analysis.note_count:,}  {_S.DIM}(polyphony: {prepared.analysis.max_polyphony}){_S.RESET}")
    ap.row("Transpose", f"{prepared.analysis.transpose_semitones:+d} semitones")

    ap.section("Playback Plan")
    ap.row(
        "Instrument",
        (
            f"{instrument_profile.name}"
            f"  {_S.DIM}({cfg.connected_motors}/{instrument_profile.motor_count} motors){_S.RESET}"
        ),
    )
    ap.row("Event groups", f"{summary['playback_plan']['event_group_count']:,}")
    ap.row("Motor changes", f"{summary['playback_plan']['motor_change_count']:,}")
    ap.row(
        "Min / p90",
        f"{summary['playback_plan']['min_delta_us']} us / {summary['playback_plan']['p90_delta_us']} us",
    )
    ap.row(
        "Median / p95",
        f"{summary['playback_plan']['median_delta_us']} us / {summary['playback_plan']['p95_delta_us']} us",
    )
    ap.row("Max", f"{summary['playback_plan']['max_delta_us']:,} us")
    ap.row("<=1ms", str(short_counts["<=1ms"]))
    ap.row("<=2ms", str(short_counts["<=2ms"]))
    ap.row("Duration delta", f"{duration_delta_us:+d} us")

    ap.section("Allocation")
    ap.row("Policy", str(prepared.compiled.overflow_mode))
    ap.row("Steals / Drops", f"{prepared.compiled.stolen_note_count} / {prepared.compiled.dropped_note_count}")
    ap.row("Truncated / Zero", f"{prepared.compiled.truncated_note_count} / {prepared.compiled.zero_length_note_count}")
    ap.row(
        "Seg normalize",
        f"merged {prepared.compiled.adjacent_segments_merged}, absorbed {prepared.compiled.short_segments_absorbed}",
    )
    if prepared.compiled.direction_flip_requested_count > 0:
        ap.row(
            "Dir flips",
            (
                f"{prepared.compiled.direction_flip_requested_count} requested / "
                f"{prepared.compiled.direction_flip_applied_count} applied / "
                f"{prepared.compiled.direction_flip_suppressed_count} suppressed / "
                f"{prepared.compiled.direction_flip_cooldown_suppressed_count} cooldown"
            ),
        )
    if prepared.compiled.tight_boundary_warning_count > 0:
        ap.row(
            "Tight windows",
            f"{prepared.compiled.tight_boundary_warning_count} event boundaries",
        )
    ap.section("Arrangement")
    ap.row(
        "Melody",
        (
            f"{prepared.arrangement_report.preserved_melody_note_count}/{prepared.arrangement_report.melody_note_count} kept"
            f"  {_S.DIM}({prepared.arrangement_report.dropped_melody_note_count} dropped){_S.RESET}"
        ),
    )
    ap.row(
        "Bass / Inner",
        (
            f"{prepared.arrangement_report.preserved_bass_note_count}/{prepared.arrangement_report.bass_note_count} bass kept"
            f"  {_S.DIM}(inner drops {prepared.arrangement_report.dropped_inner_note_count}){_S.RESET}"
        ),
    )
    ap.row(
        "Retarget / Coalesce",
        (
            f"{prepared.arrangement_report.octave_retargeted_note_count} / "
            f"{prepared.arrangement_report.coalesced_transition_count}"
        ),
    )
    ap.row(
        "Reversal pressure",
        (
            f"{prepared.arrangement_report.requested_reversal_count} requested / "
            f"{prepared.arrangement_report.applied_reversal_count} applied / "
            f"{prepared.arrangement_report.avoided_reversal_count} avoided"
        ),
    )
    ap.row(
        "Comfort hits",
        (
            f"{prepared.arrangement_report.motor_comfort_violation_count}"
            f"  {_S.DIM}(preferred {prepared.arrangement_report.motor_preferred_band_violation_count}, "
            f"resonance {prepared.arrangement_report.motor_resonance_band_hit_count}, "
            f"avoid {prepared.arrangement_report.motor_avoid_band_hit_count}){_S.RESET}"
        ),
    )
    ap.row("Weighted loss", f"{prepared.arrangement_report.weighted_musical_loss:.2f}")
    ap.row("Avg active", f"{prepared.avg_active:.2f}")
    ap.blank()

    ap.emit()
    return 0


def render_wav_command(args: argparse.Namespace) -> int:
    cfg = _build_config(args)
    instrument_profile = _load_selected_instrument_profile(cfg)
    midi_path = Path(args.midi_path)
    if not midi_path.exists():
        raise RuntimeError(f"MIDI file not found: {midi_path}")

    out_wav = Path(args.out) if args.out else midi_path.with_suffix(".stepper.wav")
    options = RenderWavOptions(
        sample_rate=int(args.sample_rate),
        normalize=not bool(args.no_normalize),
        firmware_emulate=True,
        max_accel_dhz_per_s=int(args.max_accel_dhz_per_s),
        safe_max_freq_hz=(
            float(args.safe_max_freq)
            if args.safe_max_freq is not None
            else float(cfg.max_freq_hz)
        ),
        clamp_frequencies=bool(args.clamp_frequencies),
    )
    result = render_midi_to_stepper_wav(
        midi_path=midi_path,
        cfg=cfg,
        out_wav=out_wav,
        options=options,
    )

    panel = _Panel(title="music2 · render-wav")
    panel.section("Output")
    panel.row("Input", midi_path.name)
    panel.row(
        "Instrument",
        (
            f"{instrument_profile.name}"
            f"  {_S.DIM}({cfg.connected_motors}/{instrument_profile.motor_count} motors){_S.RESET}"
        ),
    )
    panel.row("WAV", str(result.wav_path))
    panel.row("Metadata", str(result.metadata_path))
    panel.section("Audio")
    panel.row("Duration", _fmt_time(result.duration_s))
    panel.row("Sample rate", f"{result.sample_rate:,} Hz")
    panel.row("Segments", f"{result.segment_count:,}")
    panel.row("Peak / RMS", f"{result.peak:.3f} / {result.rms:.3f}")
    panel.row("Transpose", f"{cfg.transpose_override:+d}" if cfg.transpose_override is not None else "auto")
    panel.row("Clamp freqs", "on" if options.clamp_frequencies else "off")
    panel.blank()
    panel.emit()
    return 0


def speech_analyze_command(args: argparse.Namespace) -> int:
    _, preset, playback = _build_speech_playback(args)
    render_result = None
    evaluation = None
    if getattr(args, "evaluate", False):
        out_wav = Path(args.out) if getattr(args, "out", None) else _default_speech_out_path(playback.utterance.source_text)
        render_result = render_speech_to_wav(
            playback=playback,
            out_wav=out_wav,
            options=RenderWavOptions(
                sample_rate=int(getattr(args, "sample_rate", 48_000)),
                normalize=not bool(getattr(args, "no_normalize", False)),
                clamp_frequencies=True,
            ),
        )
        evaluation = evaluate_render(playback=playback, render=render_result)
    if getattr(args, "json", False):
        _out(
            json.dumps(
                _speech_json_payload(
                    playback,
                    preset_id=preset.preset_id,
                    render_result=render_result,
                    evaluation=evaluation,
                ),
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    _emit_speech_panel(
        "music2 · speech-analyze",
        playback=playback,
        preset_id=preset.preset_id,
        render_result=render_result,
        evaluation=evaluation,
    )
    return 0


def speech_render_wav_command(args: argparse.Namespace) -> int:
    _, preset, playback = _build_speech_playback(args)
    out_wav = Path(args.out) if args.out else Path(f"{_slugify_text(playback.utterance.source_text)}.speech.wav")
    render_result = render_speech_to_wav(
        playback=playback,
        out_wav=out_wav,
        options=RenderWavOptions(
            sample_rate=int(args.sample_rate),
            normalize=not bool(args.no_normalize),
            clamp_frequencies=True,
        ),
    )
    evaluation = evaluate_render(playback=playback, render=render_result) if getattr(args, "evaluate", False) else None
    if getattr(args, "json", False):
        _out(
            json.dumps(
                _speech_json_payload(
                    playback,
                    preset_id=preset.preset_id,
                    render_result=render_result,
                    evaluation=evaluation,
                ),
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    _emit_speech_panel(
        "music2 · speech-render-wav",
        playback=playback,
        preset_id=preset.preset_id,
        render_result=render_result,
        evaluation=evaluation,
    )
    return 0


def speech_preview_command(args: argparse.Namespace) -> int:
    _, preset, playback = _build_speech_playback(args)
    render_result = None
    if not getattr(args, "no_render", False):
        out_wav = Path(args.out) if args.out else _default_speech_out_path(playback.utterance.source_text)
        render_result = render_speech_to_wav(
            playback=playback,
            out_wav=out_wav,
            options=RenderWavOptions(
                sample_rate=int(args.sample_rate),
                normalize=not bool(args.no_normalize),
                clamp_frequencies=True,
            ),
        )
    if getattr(args, "json", False):
        _out(
            json.dumps(
                _speech_json_payload(
                    playback,
                    preset_id=preset.preset_id,
                    render_result=render_result,
                ),
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    _emit_speech_panel(
        "music2 · speech-preview",
        playback=playback,
        preset_id=preset.preset_id,
        render_result=render_result,
    )
    return 0


def speech_run_command(args: argparse.Namespace) -> int:
    cfg = _build_speech_host_config(args)
    _, preset, playback = _build_speech_playback(args)
    if cfg.auto_home and cfg.home_hz > 200.0 and not getattr(args, "allow_high_home_hz", False):
        raise RuntimeError(
            f"speech run home_hz={cfg.home_hz:.1f} exceeds 200 Hz safety gate; use --allow-high-home-hz if intentional"
        )
    runner = PlaybackRunner(
        port=cfg.port,
        baudrate=cfg.baudrate,
        timeout_s=cfg.timeout_s,
        write_timeout_s=cfg.write_timeout_s,
        retries=cfg.retries,
    )
    with runner.session() as session:
        session.validate(connected_motors=6, requires_direction_flip=False)
        _emit_speech_panel("music2 · speech-run", playback=playback, preset_id=preset.preset_id)
        capability_flags = int(getattr(session.capabilities, "feature_flags", 0))
        speech_assist_enabled = playback.report.engine_id == "acoustic_v2" and _supports_speech_assist(capability_flags)
        session.setup(
            motors=6,
            idle_mode=cfg.idle_mode,
            min_note=0,
            max_note=127,
            transpose=0,
            playback_run_accel_hz_per_s=cfg.playback_run_accel_hz_per_s,
            playback_launch_start_hz=cfg.playback_launch_start_hz,
            playback_launch_accel_hz_per_s=cfg.playback_launch_accel_hz_per_s,
            playback_launch_crossover_hz=cfg.playback_launch_crossover_hz,
            speech_assist_control_interval_us=(
                preset.speech_assist_control_interval_us if speech_assist_enabled else None
            ),
            speech_assist_release_accel_hz_per_s=(
                preset.speech_assist_release_accel_hz_per_s if speech_assist_enabled else None
            ),
        )
        if playback.report.engine_id == "acoustic_v2" and not speech_assist_enabled:
            _out(f"  {_S.DIM}Speech assist not advertised by firmware; using host-only acoustic_v2 playback.{_S.RESET}")
        if not _prompt_play(args):
            _out(f"\n  {_S.DIM}Canceled before speech playback start.{_S.RESET}\n")
            return 0
        progress = _LiveProgress(
            total_segments=playback.playback_plan.event_group_count,
            duration_s=playback.report.duration_s,
            queue_capacity=session.capabilities.queue_capacity,
        )
        observer = CallbackPlaybackObserver(
            on_progress_cb=progress.update,
            on_complete_cb=lambda latest_metrics, last_progress: progress.finish(last_progress) if last_progress is not None else None,
        )
        result = session.execute_plan(
            playback_plan=playback.playback_plan,
            lookahead_ms=cfg.lookahead_ms,
            lookahead_strategy=cfg.lookahead_strategy,
            lookahead_min_ms=cfg.lookahead_min_ms,
            lookahead_percentile=cfg.lookahead_percentile,
            lookahead_min_segments=cfg.lookahead_min_segments,
            metrics_poll_interval_s=0.1,
            status_poll_interval_s=0.05,
            scheduled_start_guard_ms=cfg.scheduled_start_guard_ms,
            clock_sync_samples=8,
            startup_countdown_s=cfg.startup_countdown_s,
            run_countdown=lambda seconds: _start_playback_countdown(args, seconds=seconds),
            auto_home_enabled=cfg.auto_home,
            run_auto_home=lambda client: _run_auto_home(client, cfg),
            warmup_step_motion_routines=[],
            warmup_require_home_before_sequence=False,
            warmup_requires_directional_exact_motion=False,
            observer=observer,
        )
    mp = _Panel(title="music2 · speech metrics")
    mp.section("Metrics")
    mp.row("Underruns", str(result.metrics.underrun_count))
    mp.row("CRC errors", str(result.metrics.crc_parse_errors))
    mp.row("RX errors", str(result.metrics.rx_parse_errors))
    mp.row("Guard hits", str(result.metrics.scheduler_guard_hits))
    mp.row("Engine faults", str(result.metrics.engine_fault_count))
    if bool(getattr(result.capabilities, "home_supported", False)) and result.auto_home_skipped_reason is not None:
        mp.row("Auto-home", f"skipped: {result.auto_home_skipped_reason}")
    elif bool(getattr(result.capabilities, "home_supported", False)) and result.auto_home_error is not None:
        mp.row("Auto-home", f"failed: {result.auto_home_error}")
    mp.blank()
    mp.emit()
    return 0


def speech_corpus_command(args: argparse.Namespace) -> int:
    speech_cfg = load_speech_config(getattr(args, "speech_config", "config.speech.toml"))
    corpus_path = Path(getattr(args, "corpus", None) or speech_cfg.corpus_path or DEFAULT_CORPUS_PATH)
    entries = load_corpus(corpus_path)
    render_dir = Path(args.out_dir) if args.out_dir else Path(".cache") / "speech_corpus"
    render_dir.mkdir(parents=True, exist_ok=True)
    evaluations = []
    do_evaluate = bool(getattr(args, "evaluate", False))
    engine = getattr(args, "engine", None) or speech_cfg.default_engine
    for entry in entries:
        preset = load_speech_preset(entry.preset)
        utterance = utterance_from_text(
            entry.text,
            voice=entry.voice,
            backend="auto",
            word_gap_ms=preset.word_gap_ms,
            pause_ms=preset.pause_ms,
        )
        playback = compile_utterance(utterance, preset=preset, engine=engine)
        render_result = render_speech_to_wav(
            playback=playback,
            out_wav=render_dir / f"{entry.phrase_id}.speech.wav",
            options=RenderWavOptions(
                sample_rate=int(args.sample_rate),
                normalize=not bool(args.no_normalize),
                clamp_frequencies=True,
            ),
        )
        if do_evaluate:
            evaluations.append(evaluate_render(playback=playback, render=render_result))
    summary = summarize_corpus(tuple(evaluations)) if do_evaluate else None
    if getattr(args, "json", False):
        _out(
            json.dumps(
                {
                    "corpus_path": str(corpus_path),
                    "recognizer": summary.recognizer if summary is not None else "skipped",
                    "available": summary.available if summary is not None else False,
                    "average_word_accuracy": summary.average_word_accuracy if summary is not None else 0.0,
                    "entries": [
                        {
                            "target_text": item.target_text,
                            "recognized_text": item.recognized_text,
                            "word_accuracy": item.word_accuracy,
                            "available": item.available,
                        }
                        for item in (summary.entries if summary is not None else ())
                    ],
                    "notes": list(summary.notes) if summary is not None else ["evaluation skipped; rerun with --evaluate"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    panel = _Panel(title="music2 · speech-corpus")
    panel.section("Corpus")
    panel.row("Entries", str(len(entries)))
    panel.row("Recognizer", summary.recognizer if summary is not None else "skipped")
    panel.row("Average acc", f"{(summary.average_word_accuracy if summary is not None else 0.0) * 100:.1f}%")
    panel.row("Render dir", str(render_dir))
    if summary is None:
        panel.section("Notes")
        panel.raw("evaluation skipped; rerun with --evaluate")
    elif summary.notes:
        panel.section("Notes")
        for note in summary.notes:
            panel.raw(note)
    panel.blank()
    panel.emit()
    return 0


def simulate_command(args: argparse.Namespace) -> int:
    cfg = _build_config(args)
    instrument_profile = _load_selected_instrument_profile(cfg)
    midi_path = Path(args.midi_path)
    if not midi_path.exists():
        raise RuntimeError(f"MIDI file not found: {midi_path}")

    prepared = _prepare_playback_artifacts(
        cfg=cfg,
        midi_path=midi_path,
        instrument_profile=instrument_profile,
    )
    payload = simulate_playback_program(
        playback_program=prepared.playback_program,
        instrument_profile=instrument_profile,
    )
    if args.out:
        Path(args.out).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        _out(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    section = payload["sections"][0] if payload.get("sections") else {}
    summary = section.get("summary", {})
    panel = _Panel(title="music2 · simulate")
    panel.section("Program")
    panel.row("File", midi_path.name)
    panel.row("Mode", payload.get("display_name", "unknown"))
    panel.row("Sections", str(payload.get("section_count", 0)))
    panel.section("Summary")
    panel.row("Event groups", str(summary.get("event_group_count", 0)))
    panel.row("Duration", f"{summary.get('duration_total_us', 0)} us")
    panel.row("Motor changes", str(summary.get("motor_change_count", 0)))
    panel.row("Risk hits", str(summary.get("risk_hit_count", 0)))
    panel.row("Flip count", str(summary.get("flip_count", 0)))
    if args.out:
        panel.row("Output", str(Path(args.out).resolve()))
    panel.blank()
    panel.emit()
    return 0


def replay_command(args: argparse.Namespace) -> int:
    payload = import_run_bundle(args.bundle, out_path=args.out if args.out else None)
    if args.json:
        _out(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    panel = _Panel(title="music2 · replay")
    panel.section("Bundle")
    panel.row("Source", str(Path(args.bundle).resolve()))
    panel.row("Replay ID", str(payload.get("replay_id", "")))
    panel.row("Type", str(payload.get("source_bundle_type", "")))
    panel.row("Status samples", str(len(payload.get("status_trace", []))))
    panel.row("Metrics samples", str(len(payload.get("metrics_trace", []))))
    if args.out:
        panel.row("Output", str(Path(args.out).resolve()))
    panel.blank()
    panel.emit()
    return 0


def compare_run_command(args: argparse.Namespace) -> int:
    payload = compare_plan_to_replay(
        simulated_path=args.simulated,
        replay_path=args.replay,
        out_path=args.out if args.out else None,
    )
    if args.json:
        _out(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    _out(payload["summary_markdown"])
    return 0


def doctor_command(args: argparse.Namespace) -> int:
    cfg = _build_config(args)
    checks: list[dict[str, object]] = []

    py_version = sys.version.split()[0]
    is_py311 = py_version.startswith("3.11.")
    checks.append(
        {
            "name": "python_version",
            "ok": is_py311 or not args.strict,
            "details": f"{py_version} (transcribe stack is best on 3.11)",
            "severity": "warn" if not is_py311 else "ok",
        }
    )
    checks.append(
        {
            "name": "python_executable",
            "ok": True,
            "details": sys.executable,
            "severity": "ok",
        }
    )
    checks.append(
        {
            "name": "python_site_packages",
            "ok": True,
            "details": sysconfig.get_paths().get("purelib", "unknown"),
            "severity": "ok",
        }
    )
    checks.append(
        {
            "name": "ffmpeg",
            "ok": shutil.which("ffmpeg") is not None,
            "details": shutil.which("ffmpeg") or "missing from PATH",
            "severity": "error" if shutil.which("ffmpeg") is None else "ok",
        }
    )

    static_dir = Path(cfg.ui_static_dir).expanduser().resolve()
    checks.append(
        {
            "name": "ui_dist",
            "ok": static_dir.exists(),
            "details": str(static_dir),
            "severity": "error" if not static_dir.exists() else "ok",
        }
    )

    serial_candidates = sorted(str(path) for path in Path("/dev").glob("cu.*"))
    checks.append(
        {
            "name": "serial_devices",
            "ok": len(serial_candidates) > 0,
            "details": ", ".join(serial_candidates[:8]) if serial_candidates else "none detected",
            "severity": "warn" if len(serial_candidates) == 0 else "ok",
        }
    )

    transcribe_imports = {
        "basic_pitch": "basic_pitch",
        "demucs": "demucs",
        "librosa": "librosa",
        "torch": "torch",
        "torchcrepe": "torchcrepe",
        "piano_transcription_inference": "piano_transcription_inference",
    }
    for name, module_name in transcribe_imports.items():
        try:
            __import__(module_name)
            ok = True
            details = "available"
        except Exception as exc:  # pragma: no cover - environment dependent
            ok = False
            details = str(exc)
        checks.append(
            {
                "name": f"dep:{name}",
                "ok": ok or not args.strict,
                "details": details,
                "severity": "warn" if not ok else "ok",
            }
        )

    speech_cfg_path = Path("config.speech.toml").resolve()
    speech_espeak = espeak_available()
    checks.append(
        {
            "name": "speech:config",
            "ok": speech_cfg_path.exists() or not args.strict,
            "details": str(speech_cfg_path),
            "severity": "warn" if not speech_cfg_path.exists() else "ok",
        }
    )
    checks.append(
        {
            "name": "speech:espeak",
            "ok": speech_espeak or not args.strict,
            "details": "available" if speech_espeak else "not installed (rules fallback still works)",
            "severity": "warn" if not speech_espeak else "ok",
        }
    )

    failed = [check for check in checks if not bool(check["ok"])]
    status = "ok" if not failed else "issues"
    payload = {
        "status": status,
        "platform": platform.platform(),
        "config_path": str(Path(args.config).resolve()),
        "checks": checks,
    }
    if args.json:
        _out(json.dumps(payload, indent=2, sort_keys=True))
    else:
        dp = _Panel(title="music2 · doctor")
        dp.blank()
        for check in checks:
            color = _S.GREEN if check["ok"] else (_S.YELLOW if check["severity"] == "warn" else _S.RED)
            indicator = f"{color}{'ok' if check['ok'] else 'FAIL'}{_S.RESET}"
            dp.row(str(check["name"]), f"{indicator}  {_S.DIM}{check['details']}{_S.RESET}")
        dp.blank()
        dp.emit()

    if failed:
        return 2
    return 0


def find_song_command(args: argparse.Namespace) -> int:
    from .song_lookup.pipeline import find_song
    from .song_lookup.reporting import format_lookup_result
    from .song_lookup.types import SongQuery

    cfg = _build_config(args)
    query = SongQuery(
        title=args.title,
        artist=args.artist or None,
        preferred_source_kind=args.prefer_source,
        max_candidates=args.max_candidates,
        allow_audio_fallback=bool(args.allow_audio_fallback),
        local_only=bool(args.local_only),
        manual_urls=tuple(args.manual_url or []),
        manual_paths=tuple(args.manual_path or []),
        audio_paths=tuple(args.audio_path or []),
    )
    result = find_song(
        query,
        cfg=cfg,
        cache_root=args.cache_dir,
        out_dir=args.out_dir,
        download_best=args.download_best,
        download_top=args.download_top,
    )
    if args.json:
        _out(json.dumps(result.to_json_dict(), indent=2, sort_keys=True))
    else:
        _out(format_lookup_result(result) + "\n")
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="music2")
    subparsers = parser.add_subparsers(dest="command", required=True)
    speech_preset_choices = list(available_preset_ids())

    run_parser = subparsers.add_parser("run", help="Analyze and stream a MIDI file")
    run_parser.add_argument("midi_path")
    run_parser.add_argument("--config", default="config.toml")
    run_parser.add_argument("--profile", choices=sorted(_PIPELINE_PROFILES.keys()), default=None)
    run_parser.add_argument("--instrument-profile", default=None, help="Instrument calibration profile TOML")

    run_parser.add_argument("--port", default=None)
    run_parser.add_argument("--baud", type=int, default=None)
    run_parser.add_argument("--timeout", type=float, default=None)
    run_parser.add_argument("--write-timeout", type=float, default=None)
    run_parser.add_argument("--retries", type=int, default=None)

    run_parser.add_argument("--motors", type=int, default=None)
    run_parser.add_argument("--idle-mode", choices=["idle", "duplicate"], default=None)
    run_parser.add_argument("--overflow-mode", choices=["steal_quietest", "drop_newest", "strict"], default=None)

    run_parser.add_argument("--min-freq", type=float, default=None)
    run_parser.add_argument("--max-freq", type=float, default=None)
    run_parser.add_argument("--transpose", type=int, default=None)
    run_parser.add_argument("--no-auto-transpose", action="store_true")
    run_parser.add_argument("--sticky-gap-ms", type=int, default=None)
    run_parser.add_argument("--lookahead-ms", type=int, default=None)
    run_parser.add_argument("--lookahead-strategy", choices=["average", "p90", "p95", "percentile"], default=None)
    run_parser.add_argument("--lookahead-min-ms", type=int, default=None)
    run_parser.add_argument("--lookahead-percentile", type=int, default=None)
    run_parser.add_argument("--lookahead-min-segments", type=int, default=None)
    run_parser.add_argument("--home-steps-per-rev", type=int, default=None)
    run_parser.add_argument("--home-hz", type=float, default=None)
    run_parser.add_argument("--home-start-hz", type=float, default=None)
    run_parser.add_argument("--home-accel-hz-per-s", type=float, default=None)
    run_parser.add_argument("--playback-run-accel-hz-per-s", type=float, default=None)
    run_parser.add_argument("--playback-launch-start-hz", type=float, default=None)
    run_parser.add_argument("--playback-launch-accel-hz-per-s", type=float, default=None)
    run_parser.add_argument("--playback-launch-crossover-hz", type=float, default=None)
    run_parser.add_argument("--allow-high-home-hz", action="store_true")
    run_parser.add_argument("--ui", action="store_true", help="Enable FastAPI + React dashboard during playback")
    run_parser.add_argument("--ui-host", default=None, help="UI server host (default from config)")
    run_parser.add_argument("--ui-port", type=int, default=None, help="UI server port (default from config)")
    run_parser.add_argument("--ui-static-dir", default=None, help="Built frontend directory (default from config)")
    run_parser.add_argument(
        "--ui-theme",
        choices=list(THEME_IDS),
        default=None,
        help="Viewer theme (default from config)",
    )
    run_parser.add_argument(
        "--ui-render-mode",
        choices=["prerender-30", "live"],
        default="prerender-30",
        help="Viewer mode: precomputed 30fps timeline (default) or live WS frames",
    )
    run_parser.add_argument("--ui-high-rate", action="store_true", help="Higher-rate telemetry for smoother UI")

    run_parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip interactive transpose studio / Enter-to-start prompt",
    )
    run_parser.set_defaults(handler=run_command)

    preview_parser = subparsers.add_parser("ui-preview", help="Serve UI viewer for a MIDI file without hardware playback")
    preview_parser.add_argument("midi_path")
    preview_parser.add_argument("--config", default="config.toml")
    preview_parser.add_argument("--profile", choices=sorted(_PIPELINE_PROFILES.keys()), default=None)
    preview_parser.add_argument("--instrument-profile", default=None, help="Instrument calibration profile TOML")
    preview_parser.add_argument("--motors", type=int, default=None)
    preview_parser.add_argument("--idle-mode", choices=["idle", "duplicate"], default=None)
    preview_parser.add_argument("--overflow-mode", choices=["steal_quietest", "drop_newest", "strict"], default=None)
    preview_parser.add_argument("--min-freq", type=float, default=None)
    preview_parser.add_argument("--max-freq", type=float, default=None)
    preview_parser.add_argument("--transpose", type=int, default=None)
    preview_parser.add_argument("--no-auto-transpose", action="store_true")
    preview_parser.add_argument("--sticky-gap-ms", type=int, default=None)
    preview_parser.add_argument("--lookahead-ms", type=int, default=None)
    preview_parser.add_argument("--lookahead-strategy", choices=["average", "p90", "p95", "percentile"], default=None)
    preview_parser.add_argument("--lookahead-min-ms", type=int, default=None)
    preview_parser.add_argument("--lookahead-percentile", type=int, default=None)
    preview_parser.add_argument("--lookahead-min-segments", type=int, default=None)
    preview_parser.add_argument("--ui-host", default=None, help="UI server host (default from config)")
    preview_parser.add_argument("--ui-port", type=int, default=None, help="UI server port (default from config)")
    preview_parser.add_argument("--ui-static-dir", default=None, help="Built frontend directory (default from config)")
    preview_parser.add_argument(
        "--ui-theme",
        choices=list(THEME_IDS),
        default=None,
        help="Viewer theme (default from config)",
    )
    preview_parser.add_argument(
        "--ui-render-mode",
        choices=["prerender-30"],
        default="prerender-30",
        help="Viewer mode for preview (precomputed 30fps timeline)",
    )
    preview_parser.set_defaults(handler=ui_preview_command)

    analyze_parser = subparsers.add_parser("analyze", help="Analyze compile quality metrics for a MIDI file")
    analyze_parser.add_argument("midi_path")
    analyze_parser.add_argument("--config", default="config.toml")
    analyze_parser.add_argument("--profile", choices=sorted(_PIPELINE_PROFILES.keys()), default=None)
    analyze_parser.add_argument("--instrument-profile", default=None, help="Instrument calibration profile TOML")
    analyze_parser.add_argument("--motors", type=int, default=None)
    analyze_parser.add_argument("--idle-mode", choices=["idle", "duplicate"], default=None)
    analyze_parser.add_argument("--overflow-mode", choices=["steal_quietest", "drop_newest", "strict"], default=None)
    analyze_parser.add_argument("--min-freq", type=float, default=None)
    analyze_parser.add_argument("--max-freq", type=float, default=None)
    analyze_parser.add_argument("--transpose", type=int, default=None)
    analyze_parser.add_argument("--no-auto-transpose", action="store_true")
    analyze_parser.add_argument("--sticky-gap-ms", type=int, default=None)
    analyze_parser.add_argument("--lookahead-ms", type=int, default=None)
    analyze_parser.add_argument("--lookahead-strategy", choices=["average", "p90", "p95", "percentile"], default=None)
    analyze_parser.add_argument("--lookahead-min-ms", type=int, default=None)
    analyze_parser.add_argument("--lookahead-percentile", type=int, default=None)
    analyze_parser.add_argument("--lookahead-min-segments", type=int, default=None)
    analyze_parser.add_argument("--json", action="store_true")
    analyze_parser.set_defaults(handler=analyze_command)

    find_song_parser = subparsers.add_parser("find-song", help="Find and rank song MIDI candidates for music2 playback")
    find_song_parser.add_argument("title")
    find_song_parser.add_argument("--artist", default=None)
    find_song_parser.add_argument("--config", default="config.toml")
    find_song_parser.add_argument("--profile", choices=sorted(_PIPELINE_PROFILES.keys()), default=None)
    find_song_parser.add_argument("--instrument-profile", default=None, help="Instrument calibration profile TOML")
    find_song_parser.add_argument("--motors", type=int, default=None)
    find_song_parser.add_argument("--idle-mode", choices=["idle", "duplicate"], default=None)
    find_song_parser.add_argument("--overflow-mode", choices=["steal_quietest", "drop_newest", "strict"], default=None)
    find_song_parser.add_argument("--min-freq", type=float, default=None)
    find_song_parser.add_argument("--max-freq", type=float, default=None)
    find_song_parser.add_argument("--transpose", type=int, default=None)
    find_song_parser.add_argument("--no-auto-transpose", action="store_true")
    find_song_parser.add_argument("--sticky-gap-ms", type=int, default=None)
    find_song_parser.add_argument("--max-candidates", type=int, default=8)
    find_song_parser.add_argument("--prefer-source", choices=["auto", "midi", "score", "tab", "audio"], default="auto")
    find_song_parser.add_argument("--cache-dir", default=".cache/song_lookup")
    find_song_parser.add_argument("--out-dir", default=None)
    find_song_parser.add_argument("--manual-url", action="append", default=None, help="Direct URL to MIDI/score/audio candidate")
    find_song_parser.add_argument("--manual-path", action="append", default=None, help="Local MIDI/score candidate path")
    find_song_parser.add_argument("--audio-path", action="append", default=None, help="Local audio path for transcription fallback")
    find_song_parser.add_argument("--local-only", action="store_true", help="Skip live network adapters and only use manual/local sources")
    find_song_parser.add_argument("--download-best", action="store_true", help="Copy the recommended source artifact into --out-dir")
    find_song_parser.add_argument("--download-top", type=int, default=0, help="Copy the top N source artifacts into --out-dir")
    find_song_parser.add_argument("--json", action="store_true")
    audio_group = find_song_parser.add_mutually_exclusive_group()
    audio_group.add_argument("--allow-audio-fallback", dest="allow_audio_fallback", action="store_true")
    audio_group.add_argument("--no-audio-fallback", dest="allow_audio_fallback", action="store_false")
    find_song_parser.set_defaults(allow_audio_fallback=True)
    find_song_parser.set_defaults(handler=find_song_command)

    speech_common = argparse.ArgumentParser(add_help=False)
    speech_common.add_argument("--config", default="config.toml")
    speech_common.add_argument("--speech-config", default="config.speech.toml")
    speech_common.add_argument("--text", default=None)
    speech_common.add_argument("--text-file", default=None)
    speech_common.add_argument("--phonemes-file", default=None)
    speech_common.add_argument("--voice", default=None)
    speech_common.add_argument("--backend", choices=["auto", "espeak", "rules"], default=None)
    speech_common.add_argument("--engine", choices=["symbolic_v1", "acoustic_v2"], default=None)
    speech_common.add_argument("--preset", choices=speech_preset_choices or None, default=None)
    speech_common.add_argument("--sample-rate", type=int, default=48_000)
    speech_common.add_argument("--out", default=None)
    speech_common.add_argument("--no-normalize", action="store_true")
    speech_common.add_argument("--json", action="store_true")

    speech_preview_parser = subparsers.add_parser(
        "speech-preview",
        parents=[speech_common],
        help="Compile text speech mode and optionally render an offline preview WAV",
    )
    speech_preview_parser.add_argument("--no-render", action="store_true")
    speech_preview_parser.set_defaults(handler=speech_preview_command)

    speech_analyze_parser = subparsers.add_parser(
        "speech-analyze",
        parents=[speech_common],
        help="Analyze text speech compilation metrics and optionally STT-score the render",
    )
    speech_analyze_parser.add_argument("--evaluate", action="store_true")
    speech_analyze_parser.set_defaults(handler=speech_analyze_command)

    speech_render_parser = subparsers.add_parser(
        "speech-render-wav",
        parents=[speech_common],
        help="Render text speech mode to a firmware-emulated WAV plus metadata",
    )
    speech_render_parser.add_argument("--evaluate", action="store_true")
    speech_render_parser.set_defaults(handler=speech_render_wav_command)

    speech_run_parser = subparsers.add_parser(
        "speech-run",
        parents=[speech_common],
        help="Compile text speech mode and stream it to hardware via the existing playback transport",
    )
    speech_run_parser.add_argument("--port", default=None)
    speech_run_parser.add_argument("--baud", type=int, default=None)
    speech_run_parser.add_argument("--timeout", type=float, default=None)
    speech_run_parser.add_argument("--write-timeout", type=float, default=None)
    speech_run_parser.add_argument("--retries", type=int, default=None)
    speech_run_parser.add_argument("--home-steps-per-rev", type=int, default=None)
    speech_run_parser.add_argument("--home-hz", type=float, default=None)
    speech_run_parser.add_argument("--home-start-hz", type=float, default=None)
    speech_run_parser.add_argument("--home-accel-hz-per-s", type=float, default=None)
    speech_run_parser.add_argument("--startup-countdown-s", type=int, default=None)
    speech_run_parser.add_argument("--allow-high-home-hz", action="store_true")
    speech_run_parser.add_argument("--yes", action="store_true", help="Skip interactive Enter-to-start prompt")
    speech_run_parser.set_defaults(handler=speech_run_command)

    speech_corpus_parser = subparsers.add_parser(
        "speech-corpus",
        help="Render and evaluate the bundled speech phrase corpus",
    )
    speech_corpus_parser.add_argument("--speech-config", default="config.speech.toml")
    speech_corpus_parser.add_argument("--corpus", default=None)
    speech_corpus_parser.add_argument("--out-dir", default=None)
    speech_corpus_parser.add_argument("--engine", choices=["symbolic_v1", "acoustic_v2"], default=None)
    speech_corpus_parser.add_argument("--sample-rate", type=int, default=48_000)
    speech_corpus_parser.add_argument("--evaluate", action="store_true")
    speech_corpus_parser.add_argument("--no-normalize", action="store_true")
    speech_corpus_parser.add_argument("--json", action="store_true")
    speech_corpus_parser.set_defaults(handler=speech_corpus_command)

    render_wav_parser = subparsers.add_parser(
        "render-wav",
        help="Render a firmware-emulated stepper sound WAV from a MIDI file",
    )
    render_wav_parser.add_argument("midi_path")
    render_wav_parser.add_argument("--config", default="config.toml")
    render_wav_parser.add_argument("--profile", choices=sorted(_PIPELINE_PROFILES.keys()), default=None)
    render_wav_parser.add_argument("--instrument-profile", default=None, help="Instrument calibration profile TOML")
    render_wav_parser.add_argument("--motors", type=int, default=None)
    render_wav_parser.add_argument("--idle-mode", choices=["idle", "duplicate"], default=None)
    render_wav_parser.add_argument("--overflow-mode", choices=["steal_quietest", "drop_newest", "strict"], default=None)
    render_wav_parser.add_argument("--min-freq", type=float, default=None)
    render_wav_parser.add_argument("--max-freq", type=float, default=None)
    render_wav_parser.add_argument(
        "--transpose",
        type=int,
        default=12,
        help="Global transpose semitones for render-wav (default: +12)",
    )
    render_wav_parser.add_argument("--no-auto-transpose", action="store_true")
    render_wav_parser.add_argument("--sticky-gap-ms", type=int, default=None)
    render_wav_parser.add_argument("--out", default=None, help="Output WAV path (default: <midi>.stepper.wav)")
    render_wav_parser.add_argument("--sample-rate", type=int, default=48_000)
    render_wav_parser.add_argument("--safe-max-freq", type=float, default=None)
    render_wav_parser.add_argument(
        "--clamp-frequencies",
        action="store_true",
        help="Enable pipeline/safety frequency clamping (default: off)",
    )
    render_wav_parser.add_argument("--max-accel-dhz-per-s", type=int, default=100_000)
    render_wav_parser.add_argument("--no-normalize", action="store_true")
    render_wav_parser.set_defaults(handler=render_wav_command)

    simulate_parser = subparsers.add_parser("simulate", help="Simulate a playback program without hardware")
    simulate_parser.add_argument("midi_path")
    simulate_parser.add_argument("--config", default="config.toml")
    simulate_parser.add_argument("--profile", choices=sorted(_PIPELINE_PROFILES.keys()), default=None)
    simulate_parser.add_argument("--instrument-profile", default=None, help="Instrument calibration profile TOML")
    simulate_parser.add_argument("--motors", type=int, default=None)
    simulate_parser.add_argument("--idle-mode", choices=["idle", "duplicate"], default=None)
    simulate_parser.add_argument("--overflow-mode", choices=["steal_quietest", "drop_newest", "strict"], default=None)
    simulate_parser.add_argument("--min-freq", type=float, default=None)
    simulate_parser.add_argument("--max-freq", type=float, default=None)
    simulate_parser.add_argument("--transpose", type=int, default=None)
    simulate_parser.add_argument("--no-auto-transpose", action="store_true")
    simulate_parser.add_argument("--sticky-gap-ms", type=int, default=None)
    simulate_parser.add_argument("--lookahead-ms", type=int, default=None)
    simulate_parser.add_argument("--lookahead-strategy", choices=["average", "p90", "p95", "percentile"], default=None)
    simulate_parser.add_argument("--lookahead-min-ms", type=int, default=None)
    simulate_parser.add_argument("--lookahead-percentile", type=int, default=None)
    simulate_parser.add_argument("--lookahead-min-segments", type=int, default=None)
    simulate_parser.add_argument("--out", default=None, help="Optional path to write simulation JSON")
    simulate_parser.add_argument("--json", action="store_true")
    simulate_parser.set_defaults(handler=simulate_command)

    replay_parser = subparsers.add_parser("replay", help="Import a benchmark or calibration run bundle into replay JSON")
    replay_parser.add_argument("bundle")
    replay_parser.add_argument("--out", default=None, help="Optional replay JSON output path")
    replay_parser.add_argument("--json", action="store_true")
    replay_parser.set_defaults(handler=replay_command)

    compare_run_parser = subparsers.add_parser("compare-run", help="Compare a simulated plan against a replay bundle")
    compare_run_parser.add_argument("simulated", help="Simulation JSON path from `music2 simulate`")
    compare_run_parser.add_argument("--replay", required=True, help="Replay JSON path from `music2 replay`")
    compare_run_parser.add_argument("--out", default=None, help="Optional comparison JSON output path")
    compare_run_parser.add_argument("--json", action="store_true")
    compare_run_parser.set_defaults(handler=compare_run_command)

    doctor_parser = subparsers.add_parser("doctor", help="Check runtime environment and project readiness")
    doctor_parser.add_argument("--config", default="config.toml")
    doctor_parser.add_argument("--json", action="store_true")
    doctor_parser.add_argument("--strict", action="store_true", help="Treat recommended checks as hard failures")
    doctor_parser.set_defaults(handler=doctor_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (RuntimeError, SerialClientError, AllocationError, ValueError) as exc:
        _out(f"\n  {_S.RED}{_S.BOLD}Error:{_S.RESET} {exc}\n")
        return 2
    except KeyboardInterrupt:
        _out(f"\n  {_S.DIM}Interrupted.{_S.RESET}\n")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
