"""Metadata extraction tool."""

from __future__ import annotations

from typing import Annotated, Any

from langchain.tools import ToolRuntime
from langchain_core.tools import InjectedToolArg, StructuredTool

from standard_document_assistant.config import load_config
from standard_document_assistant.graphs.metadata_extraction.graph import (
    get_metadata_extraction_graph,
)
from standard_document_assistant.schemas import MetadataExtractionResult
from standard_document_assistant.tracing import (
    METADATA_EXTRACTION_TOOL_NAME,
    ainvoke_traced_graph,
    invoke_traced_graph,
)


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
        "status": "ok",
    }


def _public_result(result: dict[str, Any]) -> dict[str, Any]:
    public = MetadataExtractionResult(
        status=result.get("status", "ok") or "ok",
        source_virtual_path=result.get("source_virtual_path", ""),
        virtual_output_path=result.get("output_virtual_path", ""),
        virtual_manifest_path=result.get("manifest_virtual_path", ""),
        aggregated_summary={
            key: (result.get("aggregated") or {}).get(key, "")
            for key in ["标准号", "标准中文名称", "ics", "ccs", "标准层级", "标准性质"]
        },
        validation=result.get("validation", {}),
        errors=result.get("errors", []),
        warnings=result.get("warnings", []),
    )
    return public.model_dump()


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
    return _public_result(result)


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
    return _public_result(result)


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
    description="Extract standard metadata fields from Markdown and persist JSON artifacts.",
)
