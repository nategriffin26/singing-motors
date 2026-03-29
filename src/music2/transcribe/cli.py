from __future__ import annotations

import argparse
import json

from .pipeline import convert_mp3_to_dual_midi
from .types import ConversionConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mp3_to_midi_best",
        description="Ultra-quality MP3-to-MIDI converter with strict 6-note maximum concurrency.",
    )
    parser.add_argument("input_audio", help="Input audio file (.mp3/.wav/.flac/.m4a/.aac/.ogg)")
    parser.add_argument("--out-dir", default="assets/midi", help="Directory for output MIDI/report files")
    parser.add_argument("--cache-dir", default=".cache/transcribe", help="Directory for cached separation/transcription outputs")
    parser.add_argument("--max-polyphony", type=int, default=6, help="Maximum simultaneous notes (hard limit: 6)")
    parser.add_argument("--quality", choices=["ultra", "high", "balanced"], default="ultra")
    parser.add_argument("--mode", choices=["music", "speech"], default="music", help="Transcription mode: music (instruments) or speech (voice pitch)")
    parser.add_argument("--device", default="auto", help="Inference device hint (auto/cpu/cuda:0/mps)")
    parser.add_argument("--pitch-bend-range", type=float, default=2.0, help="Pitch-bend range in semitones for expressive MIDI")
    parser.add_argument("--seed", type=int, default=1337, help="Deterministic seed for tie-breaking")
    parser.add_argument("--mt3-cmd", default=None, help="Optional external MT3 command template using {input} and {output}")
    parser.add_argument("--no-demucs", action="store_true", help="Disable Demucs separation and run on full mix")
    parser.add_argument("--no-report", action="store_true", help="Skip writing JSON report")
    parser.add_argument("--min-note-duration-s", type=float, default=0.05)
    parser.add_argument("--min-confidence", type=float, default=0.3)
    parser.add_argument("--no-beat-quantize", action="store_true", help="Disable beat-based onset quantization")
    parser.add_argument("--beat-quantize-max-shift-s", type=float, default=0.03)
    parser.add_argument("--no-velocity-compression", action="store_true")
    parser.add_argument("--speech-start-confidence", type=float, default=0.35)
    parser.add_argument("--speech-sustain-confidence", type=float, default=0.20)
    parser.add_argument("--speech-max-pitch-jump-semitones", type=float, default=1.5)
    parser.add_argument("--speech-median-filter-window", type=int, default=5)
    parser.add_argument("--json", action="store_true", help="Print conversion summary as JSON")
    return parser


def run_from_args(args: argparse.Namespace) -> int:
    if args.max_polyphony > 6:
        raise RuntimeError("max polyphony cannot exceed 6 (physical motor limit)")
    if args.max_polyphony < 1:
        raise RuntimeError("max polyphony must be >= 1")

    config = ConversionConfig(
        mode=args.mode,
        max_polyphony=args.max_polyphony,
        quality=args.quality,
        device=args.device,
        pitch_bend_range_semitones=args.pitch_bend_range,
        seed=args.seed,
        use_demucs=not args.no_demucs,
        mt3_command=args.mt3_cmd,
        write_report=not args.no_report,
        min_note_duration_s=args.min_note_duration_s,
        min_confidence=args.min_confidence,
        quantize_to_beats=not args.no_beat_quantize,
        velocity_compression=not args.no_velocity_compression,
        beat_quantize_max_shift_s=args.beat_quantize_max_shift_s,
        speech_start_confidence=args.speech_start_confidence,
        speech_sustain_confidence=args.speech_sustain_confidence,
        speech_max_pitch_jump_semitones=args.speech_max_pitch_jump_semitones,
        speech_median_filter_window=args.speech_median_filter_window,
    )

    result = convert_mp3_to_dual_midi(
        args.input_audio,
        output_dir=args.out_dir,
        cache_dir=args.cache_dir,
        config=config,
    )

    payload = {
        "motor_midi_path": str(result.motor_midi_path),
        "expressive_midi_path": str(result.expressive_midi_path),
        "report_path": str(result.report_path) if result.report_path else None,
        "stats": result.stats.to_json_dict(),
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"motor MIDI:      {payload['motor_midi_path']}")
        print(f"expressive MIDI: {payload['expressive_midi_path']}")
        if payload["report_path"] is not None:
            print(f"report:          {payload['report_path']}")
        stats = payload["stats"]
        print(
            "notes: "
            f"{stats['notes_after_cap']} (from {stats['notes_fused_before_cap']} fused, "
            f"dropped {stats['dropped_by_polyphony_cap']})"
        )
        print(f"polyphony: {stats['max_polyphony_output']} / {stats['max_polyphony_requested']}")
        if stats["warnings"]:
            print("warnings:")
            for warning in stats["warnings"]:
                print(f"  - {warning}")

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run_from_args(args)


if __name__ == "__main__":
    raise SystemExit(main())
