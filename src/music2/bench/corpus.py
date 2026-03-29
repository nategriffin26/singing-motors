from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import tomllib

from ..artifacts import safe_slug

DEFAULT_CORPUS_PATH = Path(__file__).resolve().parents[3] / "assets" / "bench" / "corpus.toml"


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    display_name: str
    category: str
    midi_path: Path
    expected_runtime_s: float
    hardware_setup: str
    unattended_safe: bool
    metrics_of_interest: tuple[str, ...]
    suite: str
    golden_start_s: float | None = None
    golden_end_s: float | None = None
    notes: str = ""

    @property
    def slug(self) -> str:
        return safe_slug(self.case_id)

    @property
    def golden_window_s(self) -> tuple[float, float] | None:
        if self.golden_start_s is None or self.golden_end_s is None:
            return None
        return (self.golden_start_s, self.golden_end_s)


@dataclass(frozen=True)
class BenchmarkCorpus:
    corpus_version: int
    title: str
    root_dir: Path
    cases: tuple[BenchmarkCase, ...]

    def get_case(self, case_id: str) -> BenchmarkCase:
        normalized = case_id.strip().lower()
        for case in self.cases:
            if case.case_id.lower() == normalized:
                return case
        raise KeyError(f"unknown benchmark case: {case_id}")

    def filter(
        self,
        *,
        category: str | None = None,
        suite: str | None = None,
    ) -> tuple[BenchmarkCase, ...]:
        out: list[BenchmarkCase] = []
        for case in self.cases:
            if category is not None and case.category != category:
                continue
            if suite is not None and case.suite != suite:
                continue
            out.append(case)
        return tuple(out)


def _coerce_case(root_dir: Path, raw: dict[str, Any]) -> BenchmarkCase:
    midi_path = (root_dir / str(raw["midi_path"])).resolve()
    return BenchmarkCase(
        case_id=str(raw["case_id"]),
        display_name=str(raw.get("display_name", raw["case_id"])),
        category=str(raw["category"]),
        midi_path=midi_path,
        expected_runtime_s=float(raw.get("expected_runtime_s", 0.0)),
        hardware_setup=str(raw.get("hardware_setup", "default-six-motor-rig")),
        unattended_safe=bool(raw.get("unattended_safe", False)),
        metrics_of_interest=tuple(str(item) for item in raw.get("metrics_of_interest", [])),
        suite=str(raw.get("suite", "default")),
        golden_start_s=float(raw["golden_start_s"]) if "golden_start_s" in raw else None,
        golden_end_s=float(raw["golden_end_s"]) if "golden_end_s" in raw else None,
        notes=str(raw.get("notes", "")),
    )


def load_benchmark_corpus(path: str | Path = DEFAULT_CORPUS_PATH) -> BenchmarkCorpus:
    corpus_path = Path(path).expanduser().resolve()
    with corpus_path.open("rb") as handle:
        raw = tomllib.load(handle)
    corpus = raw.get("corpus")
    if not isinstance(corpus, dict):
        raise ValueError("benchmark corpus must contain a [corpus] table")
    cases_raw = raw.get("cases")
    if not isinstance(cases_raw, list):
        raise ValueError("benchmark corpus must contain [[cases]] entries")
    root_dir = corpus_path.parent.parent
    cases = tuple(_coerce_case(root_dir, item) for item in cases_raw if isinstance(item, dict))
    return BenchmarkCorpus(
        corpus_version=int(corpus.get("version", 1)),
        title=str(corpus.get("title", "music2 benchmark corpus")),
        root_dir=root_dir,
        cases=cases,
    )
