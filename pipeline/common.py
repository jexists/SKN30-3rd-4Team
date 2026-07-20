"""v2 파이프라인 공통 파일·manifest 유틸리티."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, TextIO


def find_repo_root(start: Path | None = None) -> Path:
    """`plan.md`와 `pyproject.toml`이 있는 repository root를 찾는다."""
    current = (start or Path(__file__)).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / "plan.md").exists() and (candidate / "pyproject.toml").exists():
            return candidate
    raise RuntimeError(f"repository root를 찾지 못했습니다: {current}")


ROOT = find_repo_root()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def git_commit(root: Path = ROOT) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def runtime_versions(packages: tuple[str, ...] = ()) -> dict[str, Any]:
    package_versions: dict[str, str | None] = {}
    for package in packages:
        try:
            package_versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            package_versions[package] = None
    return {
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "executable": sys.executable,
        "packages": package_versions,
    }


def sha256_file(path: Path, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(block_size), b""):
            digest.update(block)
    return digest.hexdigest()


def relative_to_root(path: Path, root: Path = ROOT) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                value = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"JSONL 파싱 실패: {path}:{line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"JSON object가 아닙니다: {path}:{line_number}")
            yield value


@contextmanager
def atomic_text_writer(path: Path) -> Iterator[TextIO]:
    """같은 폴더의 `.tmp`에 쓴 뒤 성공 시 원자적으로 교체한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            yield stream
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    except Exception:
        # 실패 위치를 조사하고 재실행 여부를 판단할 수 있도록 tmp는 보존한다.
        raise


def write_json_atomic(path: Path, value: Any) -> None:
    with atomic_text_writer(path) as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def write_jsonl_record(stream: TextIO, value: dict[str, Any]) -> None:
    stream.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")


def load_pipeline_config(path: Path | None = None) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - requirements 설치 문제
        raise RuntimeError("pipeline config를 읽으려면 PyYAML이 필요합니다.") from exc
    config_path = path or ROOT / "pipeline" / "config" / "config.yaml"
    value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"pipeline config 형식 오류: {config_path}")
    return value


def resolve_repo_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path
