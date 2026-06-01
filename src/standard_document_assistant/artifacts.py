"""Application-layer artifact registry and download helpers.

Symmetric to ``uploads.py``: business tools persist files under ``/workspace/output/``;
this module registers those outputs per thread and exposes download metadata for API/SSE.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

from standard_document_assistant.constants import ARTIFACTS_DIR, OUTPUT_DIR, TMP_DIR
from standard_document_assistant.pathing import (
    ensure_within,
    host_to_virtual_path,
    safe_name,
    utc_now_iso,
    virtual_to_host_path,
)
from standard_document_assistant.schemas import ArtifactDownload, PersistedArtifactRecord

_CONTENT_TYPES = {
    ".json": "application/json",
    ".jsonl": "application/x-ndjson",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".pdf": "application/pdf",
    ".zip": "application/zip",
}

_TOOL_ARTIFACT_FIELDS: dict[str, list[tuple[str, str, str]]] = {
    "extract_standard_metadata": [
        ("metadata_json", "virtual_output_path", "标准元数据 JSON"),
        ("metadata_manifest", "virtual_manifest_path", "元数据 manifest"),
        ("metadata_annotated", "virtual_annotated_path", "langextract annotated jsonl"),
        ("metadata_normalized", "virtual_normalized_path", "langextract normalized json"),
    ],
    "parse_file_with_mineru": [
        ("markdown", "virtual_md_path", "MinerU Markdown"),
        ("parse_manifest", "virtual_manifest_path", "解析 manifest"),
        ("mineru_zip", "virtual_zip_path", "MinerU ZIP 归档"),
    ],
    "run_standard_review": [
        ("review_report", "artifacts.report", "审核报告"),
        ("review_result", "artifacts.result", "审核结果 JSON"),
        ("review_trace", "artifacts.trace", "审核 trace"),
        ("review_manifest", "artifacts.manifest", "审核 manifest"),
    ],
}


def _safe_thread_id(thread_id: str) -> str:
    return safe_name(thread_id, fallback="thread")


def _artifact_manifest_path(thread_id: str) -> Path:
    return ARTIFACTS_DIR / _safe_thread_id(thread_id) / "artifact_manifest.json"


def build_download_url(thread_id: str, artifact_id: str) -> str | None:
    api_base = os.getenv("STANDARD_DOC_ARTIFACT_API_BASE", "").rstrip("/")
    if not api_base:
        return None
    safe_thread = quote(_safe_thread_id(thread_id), safe="")
    safe_id = quote(artifact_id, safe="")
    return f"{api_base}/api/threads/{safe_thread}/artifacts/{safe_id}/download"


def _lookup_tool_result_value(tool_result: dict[str, Any], dotted_key: str) -> str:
    current: Any = tool_result
    for part in dotted_key.split("."):
        if not isinstance(current, dict):
            return ""
        current = current.get(part, "")
    return str(current or "").strip()


def _validate_output_virtual_path(virtual_path: str) -> Path:
    host_path = virtual_to_host_path(virtual_path)
    ensure_within(host_path, [OUTPUT_DIR, TMP_DIR], purpose="产物")
    if not host_path.exists() or not host_path.is_file():
        raise FileNotFoundError(f"产物文件不存在：{virtual_path}")
    return host_path


def register_thread_artifact(
    *,
    thread_id: str,
    virtual_path: str,
    tool: str,
    artifact_type: str,
    description: str = "",
    source_virtual_path: str = "",
) -> PersistedArtifactRecord:
    """Register a persisted workspace artifact for a thread."""

    host_path = _validate_output_virtual_path(virtual_path)
    content = host_path.read_bytes()
    artifact_id = uuid.uuid4().hex
    record = PersistedArtifactRecord(
        artifact_id=artifact_id,
        thread_id=_safe_thread_id(thread_id),
        tool=tool,
        artifact_type=artifact_type,
        description=description,
        virtual_path=virtual_path,
        source_virtual_path=source_virtual_path,
        stored_filename=host_path.name,
        host_path=str(host_path.resolve()),
        suffix=host_path.suffix.lower(),
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        content_type=_CONTENT_TYPES.get(host_path.suffix.lower(), "application/octet-stream"),
        download_url=build_download_url(thread_id, artifact_id),
        created_at=utc_now_iso(),
    )
    _append_artifact_manifest(record.thread_id, record)
    return record


def list_thread_artifacts(thread_id: str) -> list[PersistedArtifactRecord]:
    manifest_path = _artifact_manifest_path(thread_id)
    if not manifest_path.exists():
        return []
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = data.get("artifacts", []) if isinstance(data, dict) else []
    return [PersistedArtifactRecord.model_validate(item) for item in files if isinstance(item, dict)]


def get_thread_artifact(thread_id: str, artifact_id: str) -> PersistedArtifactRecord | None:
    for record in list_thread_artifacts(thread_id):
        if record.artifact_id == artifact_id:
            return record
    return None


def resolve_thread_artifact_path(thread_id: str, artifact_id: str) -> Path:
    record = get_thread_artifact(thread_id, artifact_id)
    if record is None:
        raise FileNotFoundError(f"未找到 thread={thread_id} 的产物 {artifact_id}")
    if record.thread_id != _safe_thread_id(thread_id):
        raise PermissionError("产物不属于当前 thread。")
    host_path = Path(record.host_path)
    if not host_path.exists():
        raise FileNotFoundError(f"产物文件已不存在：{record.virtual_path}")
    return host_path


def register_from_tool_result(
    *,
    thread_id: str,
    tool_name: str,
    tool_result: dict[str, Any],
    seen_virtual_paths: set[str] | None = None,
) -> list[PersistedArtifactRecord]:
    """Register all known artifact paths emitted by a business tool result."""

    if str(tool_result.get("status", "ok")).lower() in {"failed", "error"}:
        return []

    specs = _TOOL_ARTIFACT_FIELDS.get(tool_name, [])
    if not specs:
        return []

    seen = seen_virtual_paths or set()
    existing = {item.virtual_path for item in list_thread_artifacts(thread_id)}
    source_virtual_path = str(
        tool_result.get("source_virtual_path")
        or tool_result.get("virtual_md_path")
        or ""
    )
    records: list[PersistedArtifactRecord] = []

    for artifact_type, result_key, description in specs:
        virtual_path = _lookup_tool_result_value(tool_result, result_key)
        if not virtual_path or virtual_path in seen or virtual_path in existing:
            continue
        try:
            record = register_thread_artifact(
                thread_id=thread_id,
                virtual_path=virtual_path,
                tool=tool_name,
                artifact_type=artifact_type,
                description=description,
                source_virtual_path=source_virtual_path,
            )
        except (FileNotFoundError, ValueError, PermissionError):
            continue
        seen.add(virtual_path)
        existing.add(virtual_path)
        records.append(record)
    return records


def to_artifact_download(record: PersistedArtifactRecord) -> ArtifactDownload:
    return ArtifactDownload(
        artifact_id=record.artifact_id,
        virtual_path=record.virtual_path,
        host_path=record.host_path,
        file_name=record.stored_filename,
        download_url=record.download_url,
        local_open_hint=f"本地可直接打开：{record.host_path}",
    )


def public_artifact_record(record: PersistedArtifactRecord) -> dict[str, Any]:
    """Serialize an artifact record for SSE/API without leaking host_path by default."""

    payload = record.model_dump()
    expose_host_path = os.getenv("STANDARD_DOC_EXPOSE_HOST_PATH", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    if not expose_host_path:
        payload.pop("host_path", None)
    return payload


def describe_downloadable_artifact(
    virtual_path: str,
    *,
    thread_id: str | None = None,
    artifact_id: str | None = None,
) -> dict[str, str | None]:
    """Describe how a user can access a persisted artifact on disk or via HTTP."""
    host_path = virtual_to_host_path(virtual_path)
    download_url = None
    if thread_id and artifact_id:
        download_url = build_download_url(thread_id, artifact_id)
    else:
        api_base = os.getenv("STANDARD_DOC_ARTIFACT_API_BASE", "").rstrip("/")
        if api_base:
            download_url = f"{api_base}/artifacts/download?path={quote(virtual_path, safe='')}"
    return {
        "artifact_id": artifact_id,
        "virtual_path": virtual_path,
        "host_path": str(host_path),
        "file_name": host_path.name,
        "download_url": download_url,
        "local_open_hint": f"本地可直接打开：{host_path}",
    }


def copy_artifact_to_destination(virtual_path: str, destination: str | Path) -> Path:
    """Copy a workspace artifact to a user-chosen destination path."""
    source = virtual_to_host_path(virtual_path)
    target = Path(destination).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target


def resolve_artifact_host_path(virtual_path: str) -> Path:
    return virtual_to_host_path(virtual_path)


def ensure_virtual_path(path: Path) -> str:
    return host_to_virtual_path(path)


def register_artifacts_from_messages(
    messages: list[Any],
    *,
    thread_id: str,
    seen_message_ids: set[str] | None = None,
) -> list[PersistedArtifactRecord]:
    """Parse completed tool messages and register emitted artifacts."""

    seen_ids = seen_message_ids or set()
    registered: list[PersistedArtifactRecord] = []
    seen_paths: set[str] = set()

    for message in messages:
        message_id = str(getattr(message, "id", "") or "")
        if message_id and message_id in seen_ids:
            continue

        tool_name = getattr(message, "name", None)
        if not tool_name or tool_name not in _TOOL_ARTIFACT_FIELDS:
            continue

        status = getattr(message, "status", "success")
        if status not in {"success", "ok"}:
            continue

        content = getattr(message, "content", "")
        if not isinstance(content, str) or not content.strip():
            continue
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue

        registered.extend(
            register_from_tool_result(
                thread_id=thread_id,
                tool_name=str(tool_name),
                tool_result=payload,
                seen_virtual_paths=seen_paths,
            )
        )
        if message_id:
            seen_ids.add(message_id)
    return registered


def _append_artifact_manifest(thread_id: str, record: PersistedArtifactRecord) -> None:
    manifest_path = _artifact_manifest_path(thread_id)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    if manifest_path.exists():
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {"thread_id": _safe_thread_id(thread_id), "artifacts": []}
    else:
        data = {"thread_id": _safe_thread_id(thread_id), "artifacts": []}
    artifacts = data.setdefault("artifacts", [])
    artifacts.append(record.model_dump())
    manifest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
