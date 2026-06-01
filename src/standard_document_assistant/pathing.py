"""Workspace path helpers for business tools.

Custom tools touch the host filesystem directly, so they must enforce the same
virtual-path boundary that Deep Agents' built-in filesystem tools enforce.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from standard_document_assistant.constants import (
    INPUT_DIR,
    METADATA_OUTPUT_DIR,
    MINERU_OUTPUT_DIR,
    OUTPUT_DIR,
    PROJECT_ROOT,
    REVIEWS_OUTPUT_DIR,
    SAMPLES_DIR,
    TEMPLATES_DIR,
    TMP_DIR,
    UPLOADS_DIR,
    WORKSPACE_ROOT,
)


VIRTUAL_WORKSPACE_PREFIX = "/workspace/"


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def safe_name(value: str, *, fallback: str = "artifact") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.\-\u4e00-\u9fff]+", "_", value.strip())
    cleaned = cleaned.strip("._")
    return cleaned or fallback


def is_sensitive_path(path: Path | str) -> bool:
    name = Path(str(path)).name.lower()
    return (
        name.startswith(".env")
        or "secret" in name
        or "credential" in name
        or "token" in name
    )


def ensure_within(path: Path, roots: Iterable[Path], *, purpose: str) -> Path:
    resolved = path.resolve()
    resolved_roots = [root.resolve() for root in roots]
    if not any(resolved == root or root in resolved.parents for root in resolved_roots):
        allowed = ", ".join(str(root) for root in resolved_roots)
        raise ValueError(f"{purpose}路径不在允许范围内：{resolved}；允许范围：{allowed}")
    if is_sensitive_path(resolved):
        raise ValueError(f"拒绝访问敏感路径：{resolved.name}")
    return resolved


def virtual_to_host_path(virtual_path: str) -> Path:
    if "\\" in virtual_path or ":" in virtual_path:
        raise ValueError("工具参数必须使用 /workspace/ 虚拟路径，不能使用 Windows 盘符路径。")
    if ".." in Path(virtual_path).parts:
        raise ValueError("拒绝路径穿越。")
    if not virtual_path.startswith(VIRTUAL_WORKSPACE_PREFIX):
        raise ValueError("路径必须位于 /workspace/ 下。")
    relative = virtual_path[len(VIRTUAL_WORKSPACE_PREFIX) :]
    return (WORKSPACE_ROOT / relative).resolve()


def host_to_virtual_path(path: Path) -> str:
    resolved = path.resolve()
    root = WORKSPACE_ROOT.resolve()
    if not (resolved == root or root in resolved.parents):
        raise ValueError(f"无法转换为 /workspace/ 虚拟路径：{resolved}")
    relative = resolved.relative_to(root).as_posix()
    return f"/workspace/{relative}"


def resolve_workspace_read_path(
    file_path: str,
    *,
    allowed_roots: Iterable[Path] | None = None,
    suffixes: set[str] | None = None,
) -> tuple[Path, str]:
    if file_path.startswith(VIRTUAL_WORKSPACE_PREFIX):
        host_path = virtual_to_host_path(file_path)
        virtual_path = file_path
    else:
        path = Path(file_path)
        if path.is_absolute():
            raise ValueError("业务工具只接受 /workspace/ 虚拟路径或工作区相对路径。")
        host_path = (WORKSPACE_ROOT / file_path).resolve()
        virtual_path = host_to_virtual_path(host_path)
    roots = list(allowed_roots or [UPLOADS_DIR, SAMPLES_DIR, TEMPLATES_DIR, OUTPUT_DIR])
    host_path = ensure_within(host_path, roots, purpose="读取")
    if not host_path.exists():
        raise FileNotFoundError(f"文件不存在：{virtual_path}")
    if not host_path.is_file():
        raise ValueError(f"读取路径不是文件：{virtual_path}")
    if suffixes and host_path.suffix.lower() not in suffixes:
        raise ValueError(f"不支持的文件格式：{host_path.suffix}")
    return host_path, virtual_path


def resolve_workspace_write_path(file_path: str, *, default_dir: Path = OUTPUT_DIR) -> tuple[Path, str]:
    if file_path.startswith(VIRTUAL_WORKSPACE_PREFIX):
        host_path = virtual_to_host_path(file_path)
    else:
        path = Path(file_path)
        if path.is_absolute():
            raise ValueError("写入路径必须使用 /workspace/ 虚拟路径或输出目录相对路径。")
        host_path = (default_dir / file_path).resolve()
    host_path = ensure_within(host_path, [OUTPUT_DIR, TMP_DIR], purpose="写入")
    host_path.parent.mkdir(parents=True, exist_ok=True)
    return host_path, host_to_virtual_path(host_path)


def allocate_unique_path(directory: Path, stem: str, suffix: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / f"{safe_name(stem)}{suffix}"
    counter = 2
    while candidate.exists():
        candidate = directory / f"{safe_name(stem)}_{counter}{suffix}"
        counter += 1
    return candidate


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def mineru_output_root(output_subdir: str | None = None) -> Path:
    subdir = safe_name(output_subdir or "", fallback="")
    return MINERU_OUTPUT_DIR / subdir if subdir else MINERU_OUTPUT_DIR


def metadata_output_root() -> Path:
    return METADATA_OUTPUT_DIR


def review_output_root(output_subdir: str | None = None) -> Path:
    subdir = safe_name(output_subdir or "", fallback="")
    return REVIEWS_OUTPUT_DIR / subdir if subdir else REVIEWS_OUTPUT_DIR


def project_relative(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT.resolve()))
    except ValueError:
        return str(resolved)
