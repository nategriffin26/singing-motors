#!/usr/bin/env python3
"""Evaluate a transcribed MIDI against a reference MIDI using mir_eval.

Usage:
    python scripts/evaluate_midi.py reference.mid transcribed.mid [--tolerance 0.05]

Outputs precision, recall, F1 for:
  - Note onset only (within tolerance window)
  - Note onset + pitch (onset match AND correct MIDI note)
  - Note onset + pitch + offset (full note match)
  - Chroma accuracy (pitch class, ignoring octave errors)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import mir_eval
import numpy as np
import pretty_midi


def extract_notes(midi_path: str | Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Extract (intervals, pitches, velocities) from a MIDI file.

    Returns:
        intervals: (N, 2) array of [onset, offset] in seconds
        pitches: (N,) array of MIDI note numbers (as Hz for mir_eval)
        midi_notes: (N,) array of raw MIDI note numbers
    """
    pm = pretty_midi.PrettyMIDI(str(midi_path))
    onsets = []
    offsets = []
    pitches = []
    midi_notes = []
    velocities = []

    for instrument in pm.instruments:
        for note in instrument.notes:
            onsets.append(note.start)
            offsets.append(note.end)
            pitches.append(pretty_midi.note_number_to_hz(note.pitch))
            midi_notes.append(note.pitch)
            velocities.append(note.velocity)

    if not onsets:
        return (
            np.zeros((0, 2)),
            np.zeros(0),
            np.zeros(0, dtype=int),
        )

    intervals = np.column_stack([onsets, offsets])
    order = np.argsort(intervals[:, 0])
    return (
        intervals[order],
        np.array(pitches)[order],
        np.array(midi_notes, dtype=int)[order],
    )


def evaluate(
    ref_path: str | Path,
    est_path: str | Path,
    onset_tolerance: float = 0.05,
    offset_ratio: float = 0.2,
    offset_min_tolerance: float = 0.05,
) -> dict:
    """Compare estimated MIDI against reference using mir_eval."""
    ref_intervals, ref_pitches, ref_midi = extract_notes(ref_path)
    est_intervals, est_pitches, est_midi = extract_notes(est_path)

    results = {
        "ref_path": str(ref_path),
        "est_path": str(est_path),
        "ref_note_count": len(ref_pitches),
        "est_note_count": len(est_pitches),
    }

    if len(ref_pitches) == 0 or len(est_pitches) == 0:
        results["error"] = "empty reference or estimated note list"
        return results

    # --- Note-level metrics (onset + pitch + offset) ---
    prec, rec, f1, avg_overlap = mir_eval.transcription.precision_recall_f1_overlap(
        ref_intervals, ref_pitches,
        est_intervals, est_pitches,
        onset_tolerance=onset_tolerance,
        offset_ratio=offset_ratio,
        offset_min_tolerance=offset_min_tolerance,
        pitch_tolerance=50.0,  # cents
    )
    results["note_full"] = {"precision": prec, "recall": rec, "f1": f1, "avg_overlap": avg_overlap}

    # --- Onset-only metrics (pitch-agnostic) ---
    prec_on, rec_on, f1_on = mir_eval.transcription.onset_precision_recall_f1(
        ref_intervals, est_intervals,
        onset_tolerance=onset_tolerance,
    )
    results["onset_only"] = {"precision": prec_on, "recall": rec_on, "f1": f1_on}

    # --- Note onset + pitch (no offset) ---
    prec_np, rec_np, f1_np, _ = mir_eval.transcription.precision_recall_f1_overlap(
        ref_intervals, ref_pitches,
        est_intervals, est_pitches,
        onset_tolerance=onset_tolerance,
        pitch_tolerance=50.0,
        offset_ratio=None,  # ignore offsets
    )
    results["note_onset_pitch"] = {"precision": prec_np, "recall": rec_np, "f1": f1_np}

    # --- Chroma (pitch class) metrics - catches octave errors ---
    ref_chroma = np.array([p % 12 for p in ref_midi], dtype=float)
    est_chroma = np.array([p % 12 for p in est_midi], dtype=float)
    # Convert chroma to Hz-like values for mir_eval (use octave 4 = MIDI 60-71)
    ref_chroma_hz = np.array([pretty_midi.note_number_to_hz(int(c) + 60) for c in ref_chroma])
    est_chroma_hz = np.array([pretty_midi.note_number_to_hz(int(c) + 60) for c in est_chroma])
    prec_ch, rec_ch, f1_ch, _ = mir_eval.transcription.precision_recall_f1_overlap(
        ref_intervals, ref_chroma_hz,
        est_intervals, est_chroma_hz,
        onset_tolerance=onset_tolerance,
        pitch_tolerance=50.0,
        offset_ratio=None,
    )
    results["chroma_onset"] = {"precision": prec_ch, "recall": rec_ch, "f1": f1_ch}

    # --- Pitch distribution analysis ---
    ref_pitch_classes = [int(m % 12) for m in ref_midi]
    est_pitch_classes = [int(m % 12) for m in est_midi]
    note_names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    ref_dist = {note_names[i]: ref_pitch_classes.count(i) for i in range(12) if ref_pitch_classes.count(i) > 0}
    est_dist = {note_names[i]: est_pitch_classes.count(i) for i in range(12) if est_pitch_classes.count(i) > 0}
    results["ref_pitch_distribution"] = ref_dist
    results["est_pitch_distribution"] = est_dist

    # --- Timing stats ---
    ref_durations = ref_intervals[:, 1] - ref_intervals[:, 0]
    est_durations = est_intervals[:, 1] - est_intervals[:, 0]
    results["ref_duration_stats"] = {
        "min": float(ref_durations.min()),
        "max": float(ref_durations.max()),
        "mean": float(ref_durations.mean()),
        "median": float(np.median(ref_durations)),
    }
    results["est_duration_stats"] = {
        "min": float(est_durations.min()),
        "max": float(est_durations.max()),
        "mean": float(est_durations.mean()),
        "median": float(np.median(est_durations)),
    }

    # --- Short note analysis ---
    short_threshold = 0.03  # 30ms
    est_short = int(np.sum(est_durations < short_threshold))
    results["est_short_notes_under_30ms"] = est_short
    results["est_short_notes_pct"] = round(100.0 * est_short / len(est_durations), 1)

    return results


def format_report(results: dict) -> str:
    """Format evaluation results as a human-readable report."""
    lines = []
    lines.append("=" * 60)
    lines.append("MIDI TRANSCRIPTION EVALUATION REPORT")
    lines.append("=" * 60)
    lines.append(f"Reference: {results['ref_path']}")
    lines.append(f"Estimated: {results['est_path']}")
    lines.append(f"Reference notes: {results['ref_note_count']}")
    lines.append(f"Estimated notes: {results['est_note_count']}")
    lines.append("")

    if "error" in results:
        lines.append(f"ERROR: {results['error']}")
        return "\n".join(lines)

    for label, key in [
        ("Onset only (timing)", "onset_only"),
        ("Onset + Pitch", "note_onset_pitch"),
        ("Onset + Pitch + Offset (full)", "note_full"),
        ("Chroma onset (ignores octave)", "chroma_onset"),
    ]:
        m = results[key]
        lines.append(f"{label}:")
        lines.append(f"  Precision: {m['precision']:.3f}  Recall: {m['recall']:.3f}  F1: {m['f1']:.3f}")

    lines.append("")
    lines.append("Duration stats (seconds):")
    for label, key in [("Reference", "ref_duration_stats"), ("Estimated", "est_duration_stats")]:
        s = results[key]
        lines.append(f"  {label}: min={s['min']:.3f} max={s['max']:.3f} mean={s['mean']:.3f} median={s['median']:.3f}")

    lines.append(f"\nShort notes (<30ms): {results['est_short_notes_under_30ms']} ({results['est_short_notes_pct']}%)")
    lines.append("=" * 60)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate MIDI transcription quality")
    parser.add_argument("reference", help="Reference MIDI file")
    parser.add_argument("estimated", help="Transcribed/estimated MIDI file")
    parser.add_argument("--tolerance", type=float, default=0.05, help="Onset tolerance in seconds")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of text")
    args = parser.parse_args()

    results = evaluate(args.reference, args.estimated, onset_tolerance=args.tolerance)

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(format_report(results))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
