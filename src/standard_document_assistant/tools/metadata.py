"""Metadata extraction tool.

Design notes (2026-06-03, rev. 1)
---------------------------------
1. **args_schema**：使用 Pydantic 模型 :class:`ExtractStandardMetadataInput`
   做参数级校验，对齐 ``deep-agents-core`` skill 中
   "复杂入参用 ToolRuntime + InjectedToolArg 注入" 的最佳实践；
   业务字段 ``file_path`` / ``markdown`` 等都在 args_schema 内，Tool
   内部不再走 prompt 拼接。
2. **handle_tool_errors 语义**：在 sync / async 实现外层包
   try/except，把异常映射成 ``status="failed"`` + ``errors=[...]`` 的
   ``MetadataExtractionResult`` 字典，行为等价于
   ``ToolNode(tools, handle_tool_errors=True)``。LLM 看到的是结构化
   错误，可以自决。
3. **Runtime context 透传**：从 ``ToolRuntime.context`` 抽取
   ``trace_id`` / ``cover_metadata_hint`` / ``quality_strict`` 等
   横切关注点，组装成 ``MetadataExtractionContext`` 后通过
   ``invoke_traced_graph(..., context=...)`` 注入子图；子图节点通过
   ``Runtime[MetadataExtractionContext]`` 读取，state 不再背负。
4. **流式进度**：节点内已通过 ``get_stream_writer`` 推送 ``meta.*``
   事件，工具层不重复推送；如需在工具入口处推 ``meta.tool.start`` /
   ``meta.tool.end`` 汇总事件，由 ``runtime.stream_writer`` 写。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from langchain.tools import ToolRuntime
from langchain_core.tools import InjectedToolArg, StructuredTool
from pydantic import BaseModel, Field

from standard_document_assistant.artifacts import (
    describe_downloadable_artifact,
    register_from_tool_result,
    to_artifact_download,
)
from standard_document_assistant.config import load_config
from standard_document_assistant.graphs.metadata_extraction.graph import (
    get_metadata_extraction_graph,
)
from standard_document_assistant.graphs.metadata_extraction.state import (
    MetadataExtractionContext,
)
from standard_document_assistant.schemas import ArtifactDownload, MetadataExtractionResult
from standard_document_assistant.tracing import (
    METADATA_EXTRACTION_TOOL_NAME,
    ainvoke_traced_graph,
    invoke_traced_graph,
)


_SUMMARY_KEYS = ["标准号", "标准中文名称", "ics", "ccs", "标准层级", "标准性质"]


class ExtractStandardMetadataInput(BaseModel):
    """args_schema for :func:`extract_standard_metadata`.

    业务字段全部以 Pydantic 形式声明，由 StructuredTool 入口校验；
    ``runtime`` 由框架通过 ``InjectedToolArg`` 注入，**不**进入 args_schema，
    也**不**进 prompt。
    """

    file_path: str | None = Field(
        default=None,
        description=(
            "workspace 下 Markdown 虚拟路径（如 /workspace/output/mineru/**/*.md 或 "
            "/workspace/input/uploads/**/*.md）；与 markdown 二选一。"
        ),
    )
    markdown: str | None = Field(
        default=None,
        description="已解析 Markdown 正文；提供时可跳过读盘，与 file_path 二选一。",
    )
    scope_mode: Literal["metadata", "full"] | None = Field(
        default=None,
        description="抽取范围：metadata（截到第 4 章前，默认）或 full。",
    )
    output_filename: str | None = Field(
        default=None,
        description="可选输出文件名（不含路径），用于产物命名。",
    )
    write_artifacts: bool | None = Field(
        default=None,
        description="是否实际落盘 JSON / annotated / normalized / manifest。",
    )
    cover_metadata_hint: dict[str, Any] | None = Field(
        default=None,
        description=(
            "来自 parser 的 cover_metadata 提示；与抽取结果冲突时仅产生 "
            "quality_warnings，不会自动修改 JSON。"
        ),
    )


def _get_stream_writer(runtime: ToolRuntime | None) -> Any | None:
    """从 ToolRuntime 拿 stream_writer（LangChain 1.x 推荐写法）。"""

    if runtime is None:
        return None
    return getattr(runtime, "stream_writer", None)


def _emit_tool_event(
    event: dict[str, Any],
    *,
    writer: Any | None,
) -> None:
    """工具层流式事件包装，与 parser._emit_event 保持一致。"""

    if writer is None:
        return
    try:
        writer(event)
    except (RuntimeError, KeyError, AttributeError, TypeError, ValueError):
        return


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


def _build_runtime_context(
    *,
    runtime: ToolRuntime | None,
    file_path: str | None,
    cover_metadata_hint: dict[str, Any] | None,
) -> MetadataExtractionContext | None:
    """从 ToolRuntime 抽取横切关注点，组装 MetadataExtractionContext。

    - ``trace_id``：取自父 agent 的 trace 关联字段（如有），用于子图节点写日志。
    - ``parent_tool_call_id``：取自 tool_call_id，便于 LangSmith 反查。
    - ``cover_metadata_hint``：兜底从 Tool args 传入。
    - 其余字段（tenant_id / user_id）未来由主 agent 在 invoke 时透传。
    """

    if runtime is None:
        if cover_metadata_hint:
            return {"cover_metadata_hint": cover_metadata_hint}
        return None

    ctx: dict[str, Any] = {}
    raw_ctx = getattr(runtime, "context", None)
    if isinstance(raw_ctx, dict):
        for key in ("tenant_id", "user_id", "trace_id", "quality_strict"):
            value = raw_ctx.get(key)
            if value is not None and value != "":
                ctx[key] = value
        if raw_ctx.get("cover_metadata_hint"):
            ctx["cover_metadata_hint"] = raw_ctx["cover_metadata_hint"]
    tool_call_id = getattr(runtime, "tool_call_id", None)
    if tool_call_id:
        ctx["parent_tool_call_id"] = tool_call_id
    configurable = (runtime.config or {}).get("configurable") or {}
    if isinstance(configurable, dict):
        if not ctx.get("trace_id") and configurable.get("thread_id"):
            ctx["trace_id"] = str(configurable["thread_id"])
        if not ctx.get("user_id") and configurable.get("user_id"):
            ctx["user_id"] = str(configurable["user_id"])
    if cover_metadata_hint and "cover_metadata_hint" not in ctx:
        ctx["cover_metadata_hint"] = cover_metadata_hint
    return ctx or None


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


def _failed_result(
    *,
    message: str,
    errors: list[str] | None = None,
    runtime: ToolRuntime | None = None,
) -> dict[str, Any]:
    """构造 handle_tool_errors 等价的失败结果。"""

    public = MetadataExtractionResult(
        status="failed",
        errors=list(errors or [message]),
        warnings=[message],
    )
    payload = public.model_dump()
    if runtime is not None:
        configurable = (runtime.config or {}).get("configurable") or {}
        thread_id = configurable.get("thread_id") if isinstance(configurable, dict) else None
        if thread_id:
            register_from_tool_result(
                thread_id=str(thread_id),
                tool_name=METADATA_EXTRACTION_TOOL_NAME,
                tool_result=payload,
            )
    return payload


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
    writer = _get_stream_writer(runtime)
    _emit_tool_event(
        {"type": "meta.tool.start", "tool": METADATA_EXTRACTION_TOOL_NAME},
        writer=writer,
    )
    try:
        state = _build_initial_state(
            file_path=file_path,
            markdown=markdown,
            scope_mode=scope_mode,
            output_filename=output_filename,
            write_artifacts=write_artifacts,
            cover_metadata_hint=cover_metadata_hint,
        )
    except ValueError as exc:
        # 参数级校验失败（等价 ToolNode handle_tool_errors=True）
        return _failed_result(message=str(exc), runtime=runtime)

    graph = get_metadata_extraction_graph()
    parent_config = runtime.config if runtime is not None else None
    tool_call_id = runtime.tool_call_id if runtime is not None else None
    context = _build_runtime_context(
        runtime=runtime,
        file_path=file_path,
        cover_metadata_hint=cover_metadata_hint,
    )
    try:
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
            context=context,
        )
    except Exception as exc:
        # 子图执行失败（外部依赖 / 配置错误）→ 结构化失败 payload
        return _failed_result(
            message=f"extract_standard_metadata 执行失败：{exc}",
            errors=[str(exc)],
            runtime=runtime,
        )
    _emit_tool_event(
        {
            "type": "meta.tool.end",
            "tool": METADATA_EXTRACTION_TOOL_NAME,
            "status": result.get("status", "ok"),
        },
        writer=writer,
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
    writer = _get_stream_writer(runtime)
    _emit_tool_event(
        {"type": "meta.tool.start", "tool": METADATA_EXTRACTION_TOOL_NAME},
        writer=writer,
    )
    try:
        state = _build_initial_state(
            file_path=file_path,
            markdown=markdown,
            scope_mode=scope_mode,
            output_filename=output_filename,
            write_artifacts=write_artifacts,
            cover_metadata_hint=cover_metadata_hint,
        )
    except ValueError as exc:
        return _failed_result(message=str(exc), runtime=runtime)

    graph = get_metadata_extraction_graph()
    parent_config = runtime.config if runtime is not None else None
    tool_call_id = runtime.tool_call_id if runtime is not None else None
    context = _build_runtime_context(
        runtime=runtime,
        file_path=file_path,
        cover_metadata_hint=cover_metadata_hint,
    )
    try:
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
            context=context,
        )
    except Exception as exc:
        return _failed_result(
            message=f"extract_standard_metadata 执行失败：{exc}",
            errors=[str(exc)],
            runtime=runtime,
        )
    _emit_tool_event(
        {
            "type": "meta.tool.end",
            "tool": METADATA_EXTRACTION_TOOL_NAME,
            "status": result.get("status", "ok"),
        },
        writer=writer,
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
        "artifacts, and return aggregated results plus download info. "
        "Use ONLY with Markdown inputs at /workspace/output/mineru/**/*.md or "
        "/workspace/input/uploads/**/*.md; PDF/Word must be parsed first via "
        "parse_file_with_mineru. Do NOT pre-read the source Markdown; do NOT modify the "
        "produced JSON. Returns aggregated_summary, quality_warnings, virtual_output_path "
        "and download metadata for the main agent to summarize."
    ),
    args_schema=ExtractStandardMetadataInput,
)

