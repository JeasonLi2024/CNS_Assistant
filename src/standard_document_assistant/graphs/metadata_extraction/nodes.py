"""Nodes for the metadata extraction graph."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from standard_document_assistant.config import load_config
from standard_document_assistant.constants import METADATA_OUTPUT_DIR, OUTPUT_DIR, UPLOADS_DIR
from standard_document_assistant.graphs.metadata_extraction.langextract_runner import run_extraction
from standard_document_assistant.graphs.metadata_extraction.state import MetadataExtractionState
from standard_document_assistant.pathing import (
    allocate_unique_path,
    host_to_virtual_path,
    resolve_workspace_read_path,
    safe_name,
    utc_now_iso,
    write_json,
)
from standard_document_assistant.schemas import ArtifactManifest, ArtifactRef, StandardMetadataExtraction
from standard_document_assistant.tracing import METADATA_EXTRACTION_GRAPH_NAME

try:
    from langsmith import traceable
except ImportError:  # pragma: no cover - optional in minimal installs
    def traceable(*_args: Any, **_kwargs: Any):  # type: ignore[misc]
        def decorator(func: Any) -> Any:
            return func

        return decorator


@traceable(run_type="chain", name=f"{METADATA_EXTRACTION_GRAPH_NAME}.run_langextract")
def _traced_run_extraction(scoped_text: str) -> dict[str, Any]:
    return run_extraction(scoped_text)


def load_markdown(state: MetadataExtractionState) -> dict[str, Any]:
    markdown = state.get("markdown", "")
    if markdown:
        return {"markdown": markdown}
    source = state.get("source_virtual_path") or state.get("source_path") or ""
    if not source:
        return {"status": "failed", "errors": ["缺少 Markdown 输入。"]}
    path, virtual = resolve_workspace_read_path(
        source,
        allowed_roots=[UPLOADS_DIR, OUTPUT_DIR],
        suffixes={".md", ".markdown", ".txt"},
    )
    return {
        "source_path": str(path),
        "source_virtual_path": virtual,
        "markdown": path.read_text(encoding="utf-8", errors="ignore"),
    }


def slice_scope(state: MetadataExtractionState) -> dict[str, Any]:
    config = load_config()
    text = state.get("markdown", "")
    mode = state.get("scope_mode") or config.metadata_extraction.default_scope_mode
    if mode == "full":
        scoped = text
    else:
        scoped = _metadata_scope(text)
    encoded = scoped.encode("utf-8")
    if len(encoded) > config.metadata_extraction.scoped_text_max_bytes:
        scoped = encoded[: config.metadata_extraction.scoped_text_max_bytes].decode(
            "utf-8", errors="ignore"
        )
        return {"scoped_text": scoped, "warnings": ["元数据抽取范围超过限制，已截断。"]}
    return {"scoped_text": scoped}


def run_langextract(state: MetadataExtractionState) -> dict[str, Any]:
    try:
        scoped = state.get("scoped_text", "")
        raw = _traced_run_extraction(scoped)
        return {"langextract_raw": raw}
    except Exception as exc:
        return {"status": "failed", "errors": [f"元数据抽取模型调用失败：{exc}"]}


def aggregate_fields(state: MetadataExtractionState) -> dict[str, Any]:
    raw = dict(state.get("langextract_raw") or {})
    hint = state.get("cover_metadata_hint") or {}
    if hint:
        raw.setdefault("标准号", hint.get("standard_number", ""))
        raw.setdefault("代替标准号", hint.get("replaced_standard_number", ""))
        raw.setdefault("ics", hint.get("ics", ""))
        raw.setdefault("ccs", hint.get("ccs", ""))
        raw.setdefault("标准层级", hint.get("hierarchy_or_category", ""))
    raw["源文件"] = state.get("source_virtual_path", "")
    return {"aggregated": raw}


def validate_schema(state: MetadataExtractionState) -> dict[str, Any]:
    try:
        parsed = StandardMetadataExtraction.model_validate(state.get("aggregated") or {})
    except ValidationError as exc:
        return {
            "validation": {"valid": False, "errors": exc.errors()},
            "status": "failed",
            "errors": ["元数据 schema 校验失败。"],
        }
    return {"validation": {"valid": True, "warnings": []}, "aggregated": parsed.model_dump()}


def persist_output(state: MetadataExtractionState) -> dict[str, Any]:
    config = load_config()
    if not state.get("write_artifacts", config.metadata_extraction.write_artifacts):
        return {}
    source = state.get("source_path") or state.get("source_virtual_path") or "metadata"
    output_name = state.get("output_filename")
    if output_name:
        output_stem = Path(output_name).stem
    else:
        output_stem = safe_name(Path(source).stem, fallback="metadata") + "_metadata"
    output_path = allocate_unique_path(METADATA_OUTPUT_DIR / "json", output_stem, ".json")
    write_json(output_path, state.get("aggregated") or {})
    return {"output_path": str(output_path), "output_virtual_path": host_to_virtual_path(output_path)}


def write_manifest(state: MetadataExtractionState) -> dict[str, Any]:
    output_virtual = state.get("output_virtual_path", "")
    source_virtual = state.get("source_virtual_path", "")
    stem = safe_name(Path(output_virtual or "metadata").stem, fallback="metadata")
    manifest_path = allocate_unique_path(METADATA_OUTPUT_DIR / "manifests", stem + "_manifest", ".json")
    artifacts = []
    primary = None
    if output_virtual:
        primary = ArtifactRef(
            type="metadata_json",
            virtual_path=output_virtual,
            description="标准元数据 JSON",
        )
        artifacts.append(primary)
    manifest = ArtifactManifest(
        tool="extract_standard_metadata",
        status=state.get("status", "ok") or "ok",
        source_virtual_path=source_virtual,
        primary_artifact=primary,
        artifacts=artifacts,
        warnings=state.get("warnings", []),
        error="; ".join(state.get("errors", [])),
        created_at=utc_now_iso(),
    )
    write_json(manifest_path, manifest.model_dump())
    return {
        "manifest_path": str(manifest_path),
        "manifest_virtual_path": host_to_virtual_path(manifest_path),
        "status": state.get("status", "ok") or "ok",
    }


def _metadata_scope(text: str) -> str:
    markers = ["## 4", "# 4", "\n4 ", "\n4\t", "## 四", "# 四"]
    limits = [text.find(marker) for marker in markers if text.find(marker) > 0]
    if limits:
        return text[: min(limits)]
    return text[:20000]
