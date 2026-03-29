from __future__ import annotations

import hashlib
import json
import os
import platform
import random
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ensure_dir(path: str | Path) -> Path:
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    return target


def safe_slug(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip())
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    cleaned = cleaned.strip("-")
    return cleaned or "artifact"


def make_bundle_id(prefix: str, name: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = f"{random.randint(0, 0xFFFF):04x}"
    return f"{safe_slug(prefix)}-{safe_slug(name)}-{stamp}-{suffix}"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fingerprint_file(path: str | Path) -> dict[str, Any]:
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return {
            "path": str(target),
            "exists": False,
        }
    return {
        "path": str(target),
        "exists": True,
        "size": target.stat().st_size,
        "sha256": sha256_bytes(target.read_bytes()),
        "mtime_utc": datetime.fromtimestamp(target.stat().st_mtime, UTC).replace(microsecond=0).isoformat().replace(
            "+00:00",
            "Z",
        ),
    }


def git_commit_sha(cwd: str | Path | None = None) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
    return result.stdout.strip() or None


def git_describe(cwd: str | Path | None = None) -> str | None:
    try:
        result = subprocess.run(
            ["git", "describe", "--always", "--dirty"],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
    return result.stdout.strip() or None


def host_platform_info() -> dict[str, Any]:
    return {
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
        "pid": os.getpid(),
    }


def collect_provenance(
    *,
    cwd: str | Path | None = None,
    config_path: str | Path | None = None,
    instrument_profile_path: str | Path | None = None,
    extra_files: dict[str, str | Path] | None = None,
) -> dict[str, Any]:
    files: dict[str, Any] = {}
    if config_path is not None:
        files["config"] = fingerprint_file(config_path)
    if instrument_profile_path is not None:
        files["instrument_profile"] = fingerprint_file(instrument_profile_path)
    for key, raw_path in (extra_files or {}).items():
        files[key] = fingerprint_file(raw_path)
    return {
        "captured_at_utc": utc_now_iso(),
        "git_commit_sha": git_commit_sha(cwd=cwd),
        "git_describe": git_describe(cwd=cwd),
        "host": host_platform_info(),
        "files": files,
    }


def write_json(path: str | Path, payload: Any) -> Path:
    target = Path(path)
    ensure_dir(target.parent)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_jsonl(path: str | Path, rows: list[dict[str, Any]]) -> Path:
    target = Path(path)
    ensure_dir(target.parent)
    with target.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    return target


def append_jsonl(path: str | Path, row: dict[str, Any]) -> Path:
    target = Path(path)
    ensure_dir(target.parent)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
    return target


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    target = Path(path)
    if not target.exists():
        return rows
    for line in target.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        rows.append(json.loads(stripped))
    return rows
