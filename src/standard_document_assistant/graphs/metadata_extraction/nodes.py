"""Nodes for the metadata extraction graph."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from standard_document_assistant.artifacts import describe_downloadable_artifact
from standard_document_assistant.config import load_config
from standard_document_assistant.constants import METADATA_OUTPUT_DIR, OUTPUT_DIR, UPLOADS_DIR
from standard_document_assistant.graphs.metadata_extraction.langextract_runner import (
    build_extraction_result,
    collect_quality_warnings,
    run_extraction,
    save_langextract_outputs,
    slice_metadata_scope,
)
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
def _traced_run_extraction(scoped_text: str) -> Any:
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
    scoped = slice_metadata_scope(text, mode)
    encoded = scoped.encode("utf-8")
    warnings: list[str] = []
    if len(encoded) > config.metadata_extraction.scoped_text_max_bytes:
        scoped = encoded[: config.metadata_extraction.scoped_text_max_bytes].decode(
            "utf-8", errors="ignore"
        )
        warnings.append("元数据抽取范围超过限制，已截断。")
    return {
        "scoped_text": scoped,
        "scoped_text_chars": len(scoped),
        "warnings": warnings,
    }


def run_langextract(state: MetadataExtractionState) -> dict[str, Any]:
    if state.get("status") == "failed":
        return {}
    try:
        scoped = state.get("scoped_text", "")
        result = _traced_run_extraction(scoped)
        return {
            "langextract_result": result,
            "extracted_items": len(getattr(result, "extractions", []) or []),
        }
    except Exception as exc:
        return {"status": "failed", "errors": [f"langextract 元数据抽取失败：{exc}"]}


def aggregate_fields(state: MetadataExtractionState) -> dict[str, Any]:
    if state.get("status") == "failed":
        return {}
    result = state.get("langextract_result")
    if result is None:
        return {"status": "failed", "errors": ["langextract 未返回抽取结果。"]}

    source_label = state.get("source_virtual_path") or state.get("source_path") or ""
    aggregated = build_extraction_result(result, source_label)
    hint = state.get("cover_metadata_hint") or {}
    if hint:
        aggregated.setdefault("标准号", hint.get("standard_number", ""))
        aggregated.setdefault("代替标准号", hint.get("replaced_standard_number", ""))
        aggregated.setdefault("ics", hint.get("ics", ""))
        aggregated.setdefault("ccs", hint.get("ccs", ""))
        aggregated.setdefault("标准层级", hint.get("hierarchy_or_category", ""))

    quality_warnings = collect_quality_warnings(aggregated, hint=hint)
    return {"aggregated": aggregated, "quality_warnings": quality_warnings}


def validate_schema(state: MetadataExtractionState) -> dict[str, Any]:
    if state.get("status") == "failed":
        return {}
    aggregated = state.get("aggregated") or {}
    try:
        parsed = StandardMetadataExtraction.model_validate(aggregated)
    except ValidationError as exc:
        config = load_config()
        validation = {"valid": False, "errors": exc.errors()}
        if config.metadata_extraction.strict_validation:
            return {
                "validation": validation,
                "status": "failed",
                "errors": ["元数据 schema 校验失败。"],
            }
        return {
            "validation": validation,
            "quality_warnings": ["元数据 schema 校验存在告警，已保留 langextract 原始聚合结果，未自动修改 JSON。"],
            "aggregated": aggregated,
        }
    return {"validation": {"valid": True, "warnings": []}, "aggregated": parsed.model_dump()}


def persist_output(state: MetadataExtractionState) -> dict[str, Any]:
    if state.get("status") == "failed":
        return {}
    config = load_config()
    if not state.get("write_artifacts", config.metadata_extraction.write_artifacts):
        return {}

    source = state.get("source_path") or state.get("source_virtual_path") or "metadata"
    output_name = state.get("output_filename")
    if output_name:
        output_stem = Path(output_name).stem
    else:
        output_stem = safe_name(Path(source).stem, fallback="metadata")

    result = state.get("langextract_result")
    aggregated = state.get("aggregated") or {}
    if result is None:
        return {"status": "failed", "errors": ["缺少 langextract 结果，无法写入产物。"]}

    json_dir = METADATA_OUTPUT_DIR / "json"
    annotated_dir = METADATA_OUTPUT_DIR / "annotated"
    normalized_dir = METADATA_OUTPUT_DIR / "normalized"
    annotated_dir.mkdir(parents=True, exist_ok=True)
    normalized_dir.mkdir(parents=True, exist_ok=True)

    langextract_paths = save_langextract_outputs(
        result=result,
        annotated_dir=annotated_dir,
        normalized_dir=normalized_dir,
        output_stem=safe_name(output_stem, fallback="metadata"),
    )
    annotated_path = langextract_paths["annotated"]
    normalized_path = langextract_paths["normalized"]

    aggregated_path = allocate_unique_path(json_dir, f"{safe_name(output_stem, fallback='metadata')}_metadata", ".json")
    write_json(aggregated_path, aggregated)

    return {
        "output_path": str(aggregated_path),
        "output_virtual_path": host_to_virtual_path(aggregated_path),
        "annotated_path": str(annotated_path),
        "annotated_virtual_path": host_to_virtual_path(annotated_path),
        "normalized_path": str(normalized_path),
        "normalized_virtual_path": host_to_virtual_path(normalized_path),
    }


def write_manifest(state: MetadataExtractionState) -> dict[str, Any]:
    if state.get("status") == "failed":
        return {"status": "failed"}

    output_virtual = state.get("output_virtual_path", "")
    source_virtual = state.get("source_virtual_path", "")
    stem = safe_name(Path(output_virtual or "metadata").stem, fallback="metadata")
    manifest_path = allocate_unique_path(METADATA_OUTPUT_DIR / "manifests", stem + "_manifest", ".json")

    artifacts: list[ArtifactRef] = []
    primary = None
    artifact_specs = [
        ("metadata_json", output_virtual, "标准元数据 JSON（聚合结果）"),
        ("metadata_annotated", state.get("annotated_virtual_path", ""), "langextract annotated jsonl"),
        ("metadata_normalized", state.get("normalized_virtual_path", ""), "langextract normalized json"),
    ]
    for artifact_type, virtual_path, description in artifact_specs:
        if not virtual_path:
            continue
        ref = ArtifactRef(type=artifact_type, virtual_path=virtual_path, description=description)
        artifacts.append(ref)
        if artifact_type == "metadata_json":
            primary = ref

    manifest = ArtifactManifest(
        tool="extract_standard_metadata",
        status=state.get("status", "ok") or "ok",
        source_virtual_path=source_virtual,
        primary_artifact=primary,
        artifacts=artifacts,
        warnings=[*state.get("warnings", []), *state.get("quality_warnings", [])],
        error="; ".join(state.get("errors", [])),
        created_at=utc_now_iso(),
    )
    write_json(manifest_path, manifest.model_dump())
    payload: dict[str, Any] = {
        "manifest_path": str(manifest_path),
        "manifest_virtual_path": host_to_virtual_path(manifest_path),
        "status": state.get("status", "ok") or "ok",
    }
    if output_virtual:
        payload["download"] = describe_downloadable_artifact(output_virtual)
    return payload
