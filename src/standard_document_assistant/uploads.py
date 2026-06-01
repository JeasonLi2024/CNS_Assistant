"""Application-layer helpers for saving uploaded files into the workspace."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from standard_document_assistant.config import load_config
from standard_document_assistant.constants import UPLOADS_DIR
from standard_document_assistant.pathing import (
    allocate_unique_path,
    host_to_virtual_path,
    is_sensitive_path,
    safe_name,
    utc_now_iso,
)
from standard_document_assistant.schemas import UploadedFileRecord


def _safe_thread_id(thread_id: str) -> str:
    return safe_name(thread_id, fallback="thread")


def save_uploaded_file(
    *,
    original_filename: str,
    content: bytes,
    thread_id: str,
    content_type: str | None = None,
) -> UploadedFileRecord:
    """Persist a user upload under workspace/input/uploads/{thread_id}/."""

    config = load_config()
    filename = safe_name(Path(original_filename).name, fallback="upload")
    suffix = Path(filename).suffix.lower()
    if suffix not in config.workspace.allowed_upload_suffixes:
        allowed = ", ".join(config.workspace.allowed_upload_suffixes)
        raise ValueError(f"不支持的上传文件格式：{suffix}；允许：{allowed}")
    if is_sensitive_path(filename):
        raise ValueError(f"拒绝保存敏感文件名：{filename}")
    max_bytes = config.workspace.max_upload_size_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise ValueError(f"上传文件超过大小限制：{config.workspace.max_upload_size_mb}MB")

    upload_dir = UPLOADS_DIR / _safe_thread_id(thread_id)
    target = allocate_unique_path(upload_dir, Path(filename).stem, suffix)
    target.write_bytes(content)

    record = UploadedFileRecord(
        original_filename=original_filename,
        stored_filename=target.name,
        virtual_path=host_to_virtual_path(target),
        host_path=str(target),
        suffix=suffix,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        content_type=content_type or "",
        created_at=utc_now_iso(),
    )
    _append_upload_manifest(upload_dir, _safe_thread_id(thread_id), record)
    return record


def _append_upload_manifest(upload_dir: Path, thread_id: str, record: UploadedFileRecord) -> None:
    manifest_path = upload_dir / "upload_manifest.json"
    if manifest_path.exists():
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {"thread_id": thread_id, "files": []}
    else:
        data = {"thread_id": thread_id, "files": []}
    files = data.setdefault("files", [])
    files.append(record.model_dump())
    manifest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

