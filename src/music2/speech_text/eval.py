from __future__ import annotations

from dataclasses import dataclass
import importlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from .types import (
    SpeechCorpusEntry,
    SpeechCorpusEvaluation,
    SpeechEvaluationResult,
    SpeechPlaybackPlan,
    SpeechRenderResult,
)


@dataclass(frozen=True)
class RecognitionResult:
    text: str
    recognizer: str
    available: bool
    notes: tuple[str, ...] = ()


def _normalize_words(text: str) -> list[str]:
    return [part for part in "".join(ch.lower() if ch.isalnum() or ch.isspace() else " " for ch in text).split() if part]


def _levenshtein(lhs: list[str], rhs: list[str]) -> int:
    if not lhs:
        return len(rhs)
    if not rhs:
        return len(lhs)
    rows = list(range(len(rhs) + 1))
    for i, lhs_word in enumerate(lhs, start=1):
        prev = rows[0]
        rows[0] = i
        for j, rhs_word in enumerate(rhs, start=1):
            old = rows[j]
            cost = 0 if lhs_word == rhs_word else 1
            rows[j] = min(rows[j] + 1, rows[j - 1] + 1, prev + cost)
            prev = old
    return rows[-1]


def _recognize_with_whisper_command(wav_path: Path) -> RecognitionResult | None:
    if os.environ.get("MUSIC2_ENABLE_WHISPER_CMD", "").strip().lower() not in {"1", "true", "yes"}:
        return None
    exe = shutil.which("whisper-cpp") or shutil.which("whisper-cli") or shutil.which("whisper")
    if exe is None:
        return None
    try:
        result = subprocess.run(
            [exe, str(wav_path)],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception as exc:
        return RecognitionResult(text="", recognizer=Path(exe).name, available=False, notes=(str(exc),))
    text = result.stdout.strip().splitlines()[-1].strip() if result.stdout.strip() else ""
    return RecognitionResult(text=text, recognizer=Path(exe).name, available=bool(text))


def _recognize_with_faster_whisper(wav_path: Path) -> RecognitionResult | None:
    if os.environ.get("MUSIC2_ENABLE_FASTER_WHISPER", "").strip().lower() not in {"1", "true", "yes"}:
        return None
    try:
        module = importlib.import_module("faster_whisper")
    except Exception:
        return None
    try:
        model_name = os.environ.get("MUSIC2_FAST_WHISPER_MODEL", "tiny")
        model = module.WhisperModel(model_name, device="cpu", compute_type="int8")
        segments, _ = model.transcribe(str(wav_path), beam_size=1)
        text = " ".join(segment.text.strip() for segment in segments).strip()
        return RecognitionResult(text=text, recognizer="faster-whisper", available=bool(text))
    except Exception as exc:
        return RecognitionResult(text="", recognizer="faster-whisper", available=False, notes=(str(exc),))


def auto_recognize(wav_path: Path) -> RecognitionResult:
    for recognizer in (_recognize_with_whisper_command, _recognize_with_faster_whisper):
        result = recognizer(wav_path)
        if result is not None:
            return result
    return RecognitionResult(
        text="",
        recognizer="none",
        available=False,
        notes=("no local recognizer available; install whisper-cpp or faster-whisper for STT scoring",),
    )


def evaluate_render(
    *,
    playback: SpeechPlaybackPlan,
    render: SpeechRenderResult,
    recognizer=None,
) -> SpeechEvaluationResult:
    recognition = recognizer(render.wav_path) if recognizer is not None else auto_recognize(render.wav_path)
    target_words = _normalize_words(playback.utterance.normalized_text)
    heard_words = _normalize_words(recognition.text)
    word_error_count = _levenshtein(target_words, heard_words)
    word_count = max(1, len(target_words))
    word_accuracy = max(0.0, 1.0 - (word_error_count / float(word_count)))
    return SpeechEvaluationResult(
        target_text=playback.utterance.normalized_text,
        recognized_text=recognition.text,
        recognizer=recognition.recognizer,
        available=recognition.available,
        word_error_count=word_error_count,
        word_count=len(target_words),
        word_accuracy=word_accuracy,
        lane_usage_summary=playback.report.lane_active_ratio,
        lane_retarget_count=playback.report.lane_retarget_count,
        max_event_rate_hz=playback.report.max_event_rate_hz,
        notes=recognition.notes,
    )


def load_corpus(path: str | Path) -> tuple[SpeechCorpusEntry, ...]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = raw.get("entries", raw)
    return tuple(
        SpeechCorpusEntry(
            phrase_id=str(item["phrase_id"]),
            text=str(item["text"]),
            tags=tuple(str(tag) for tag in item.get("tags", [])),
            voice=str(item.get("voice", "en-us")),
            preset=str(item.get("preset", "robot_clear")),
        )
        for item in entries
    )


def summarize_corpus(entries: tuple[SpeechEvaluationResult, ...]) -> SpeechCorpusEvaluation:
    available = any(item.available for item in entries)
    recognizer = next((item.recognizer for item in entries if item.recognizer), "none")
    avg = sum(item.word_accuracy for item in entries) / max(1, len(entries))
    notes: list[str] = []
    if not available:
        notes.append("all corpus entries ran without STT recognition")
    return SpeechCorpusEvaluation(
        entries=entries,
        available=available,
        recognizer=recognizer,
        average_word_accuracy=avg,
        notes=tuple(notes),
    )
