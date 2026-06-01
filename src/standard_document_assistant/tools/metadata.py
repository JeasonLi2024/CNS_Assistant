"""Metadata extraction tool."""

from __future__ import annotations

from typing import Annotated, Any

from langchain.tools import ToolRuntime
from langchain_core.tools import InjectedToolArg, StructuredTool

from standard_document_assistant.artifacts import (
    describe_downloadable_artifact,
    register_from_tool_result,
    to_artifact_download,
)
from standard_document_assistant.config import load_config
from standard_document_assistant.graphs.metadata_extraction.graph import (
    get_metadata_extraction_graph,
)
from standard_document_assistant.schemas import ArtifactDownload, MetadataExtractionResult
from standard_document_assistant.tracing import (
    METADATA_EXTRACTION_TOOL_NAME,
    ainvoke_traced_graph,
    invoke_traced_graph,
)

_SUMMARY_KEYS = ["标准号", "标准中文名称", "ics", "ccs", "标准层级", "标准性质"]


def _build_initial_state(
    *,
    file_path: str | None,
    markdown: str | None,
    scope_mode: str | None,
    output_filename: str | None,
    write_artifacts: bool | None,
    cover_metadata_hint: dict[str, Any] | None,
) -> dict[str, Any]:
    if not file_path and not markdown:
        raise ValueError("file_path 与 markdown 至少提供一个。")
    config = load_config()
    return {
        "source_virtual_path": file_path or "",
        "markdown": markdown or "",
        "scope_mode": scope_mode or config.metadata_extraction.default_scope_mode,
        "output_filename": output_filename or "",
        "write_artifacts": (
            config.metadata_extraction.write_artifacts if write_artifacts is None else write_artifacts
        ),
        "cover_metadata_hint": cover_metadata_hint or {},
        "warnings": [],
        "errors": [],
        "quality_warnings": [],
        "status": "ok",
    }


def _attach_download_and_register(
    public: dict[str, Any],
    *,
    runtime: ToolRuntime | None,
) -> dict[str, Any]:
    output_virtual = public.get("virtual_output_path", "")
    thread_id = None
    if runtime is not None:
        configurable = (runtime.config or {}).get("configurable") or {}
        if isinstance(configurable, dict):
            thread_id = configurable.get("thread_id")

    if thread_id and public.get("status") == "ok":
        records = register_from_tool_result(
            thread_id=str(thread_id),
            tool_name=METADATA_EXTRACTION_TOOL_NAME,
            tool_result=public,
        )
        primary = next((item for item in records if item.artifact_type == "metadata_json"), None)
        if primary is not None:
            public["download"] = to_artifact_download(primary).model_dump()
            return public

    if output_virtual:
        public["download"] = describe_downloadable_artifact(output_virtual)
    return public


def _public_result(result: dict[str, Any], *, runtime: ToolRuntime | None = None) -> dict[str, Any]:
    aggregated = dict(result.get("aggregated") or {})
    quality_warnings = list(result.get("quality_warnings") or [])
    validation = result.get("validation") or {}
    if not validation.get("valid", True):
        quality_warnings.append("元数据 schema 校验未完全通过，请人工核对 JSON 后再使用。")

    download_payload = result.get("download")
    download = None
    output_virtual = result.get("output_virtual_path", "")
    if isinstance(download_payload, dict) and download_payload:
        download = ArtifactDownload.model_validate(download_payload)
    elif output_virtual:
        download = ArtifactDownload.model_validate(describe_downloadable_artifact(output_virtual))

    public = MetadataExtractionResult(
        status=result.get("status", "ok") or "ok",
        source_virtual_path=result.get("source_virtual_path", ""),
        virtual_output_path=output_virtual,
        virtual_manifest_path=result.get("manifest_virtual_path", ""),
        virtual_annotated_path=result.get("annotated_virtual_path", ""),
        virtual_normalized_path=result.get("normalized_virtual_path", ""),
        aggregated_summary={key: aggregated.get(key, "") for key in _SUMMARY_KEYS},
        aggregated=aggregated,
        validation=validation,
        quality_warnings=quality_warnings,
        scoped_text_chars=int(result.get("scoped_text_chars") or 0),
        extracted_items=int(result.get("extracted_items") or 0),
        download=download,
        errors=result.get("errors", []),
        warnings=[*result.get("warnings", []), *quality_warnings],
    )
    payload = public.model_dump()
    return _attach_download_and_register(payload, runtime=runtime)


def _trace_metadata(
    *,
    runtime: ToolRuntime | None,
    file_path: str | None,
    scope_mode: str | None,
) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    if file_path:
        extra["source_virtual_path"] = file_path
    if scope_mode:
        extra["scope_mode"] = scope_mode
    if runtime is not None:
        configurable = (runtime.config or {}).get("configurable") or {}
        if isinstance(configurable, dict) and configurable.get("thread_id"):
            extra["thread_id"] = configurable["thread_id"]
    return extra


def _run_extraction(
    *,
    file_path: str | None,
    markdown: str | None,
    scope_mode: str | None,
    output_filename: str | None,
    write_artifacts: bool | None,
    cover_metadata_hint: dict[str, Any] | None,
    runtime: ToolRuntime | None,
) -> dict[str, Any]:
    state = _build_initial_state(
        file_path=file_path,
        markdown=markdown,
        scope_mode=scope_mode,
        output_filename=output_filename,
        write_artifacts=write_artifacts,
        cover_metadata_hint=cover_metadata_hint,
    )
    graph = get_metadata_extraction_graph()
    parent_config = runtime.config if runtime is not None else None
    tool_call_id = runtime.tool_call_id if runtime is not None else None
    result = invoke_traced_graph(
        graph,
        state,
        parent_config=parent_config,
        tool_name=METADATA_EXTRACTION_TOOL_NAME,
        tool_call_id=tool_call_id,
        extra_metadata=_trace_metadata(
            runtime=runtime,
            file_path=file_path,
            scope_mode=scope_mode,
        ),
    )
    return _public_result(result, runtime=runtime)


async def _arun_extraction(
    *,
    file_path: str | None,
    markdown: str | None,
    scope_mode: str | None,
    output_filename: str | None,
    write_artifacts: bool | None,
    cover_metadata_hint: dict[str, Any] | None,
    runtime: ToolRuntime | None,
) -> dict[str, Any]:
    state = _build_initial_state(
        file_path=file_path,
        markdown=markdown,
        scope_mode=scope_mode,
        output_filename=output_filename,
        write_artifacts=write_artifacts,
        cover_metadata_hint=cover_metadata_hint,
    )
    graph = get_metadata_extraction_graph()
    parent_config = runtime.config if runtime is not None else None
    tool_call_id = runtime.tool_call_id if runtime is not None else None
    result = await ainvoke_traced_graph(
        graph,
        state,
        parent_config=parent_config,
        tool_name=METADATA_EXTRACTION_TOOL_NAME,
        tool_call_id=tool_call_id,
        extra_metadata=_trace_metadata(
            runtime=runtime,
            file_path=file_path,
            scope_mode=scope_mode,
        ),
    )
    return _public_result(result, runtime=runtime)


async def _extract_standard_metadata_async(
    file_path: str | None = None,
    *,
    markdown: str | None = None,
    scope_mode: str | None = None,
    output_filename: str | None = None,
    write_artifacts: bool | None = None,
    cover_metadata_hint: dict[str, Any] | None = None,
    runtime: Annotated[ToolRuntime | None, InjectedToolArg] = None,
) -> dict[str, Any]:
    return await _arun_extraction(
        file_path=file_path,
        markdown=markdown,
        scope_mode=scope_mode,
        output_filename=output_filename,
        write_artifacts=write_artifacts,
        cover_metadata_hint=cover_metadata_hint,
        runtime=runtime,
    )


def _extract_standard_metadata_sync(
    file_path: str | None = None,
    *,
    markdown: str | None = None,
    scope_mode: str | None = None,
    output_filename: str | None = None,
    write_artifacts: bool | None = None,
    cover_metadata_hint: dict[str, Any] | None = None,
    runtime: Annotated[ToolRuntime | None, InjectedToolArg] = None,
) -> dict[str, Any]:
    return _run_extraction(
        file_path=file_path,
        markdown=markdown,
        scope_mode=scope_mode,
        output_filename=output_filename,
        write_artifacts=write_artifacts,
        cover_metadata_hint=cover_metadata_hint,
        runtime=runtime,
    )


extract_standard_metadata = StructuredTool.from_function(
    func=_extract_standard_metadata_sync,
    coroutine=_extract_standard_metadata_async,
    name=METADATA_EXTRACTION_TOOL_NAME,
    description=(
        "Extract 16 standard metadata fields from Markdown via langextract, persist JSON/manifest "
        "artifacts, and return aggregated results plus download info."
    ),
)
