"""Standard review tools (Deep Agents integration).

Design notes (2026-06-03, rev. 1)
---------------------------------
1. **args_schema**：使用 Pydantic 模型（``RunStandardReviewInput`` /
   ``RunFormatSourceReviewInput`` / ``InspectReviewRulesInput`` /
   ``BuildReviewIndexInput`` / ``ValidateReviewResultSchemaInput``）做参数级
   校验，对齐 ``deep-agents-core`` skill 中"复杂入参用 ToolRuntime +
   InjectedToolArg 注入"的最佳实践；业务字段全部在 args_schema 内，Tool
   内部不再走 prompt 拼接。
2. **handle_tool_errors 语义**：在 sync / async 实现外层包 try/except，把
   异常映射成 ``status="failed"`` + ``error=...`` 的 ``ReviewToolResult`` 字
   典，行为等价于 ``ToolNode(tools, handle_tool_errors=True)``。LLM 看到的
   是结构化错误，可以自决。
3. **Runtime context 透传**：从 ``ToolRuntime.context`` 抽取 ``trace_id``
   / ``job_id`` 等横切关注点，组装成 ``StandardReviewContext`` 后通过
   ``invoke_traced_graph(..., context=...)`` 注入子图；子图节点通过
   ``Runtime[StandardReviewContext]`` 读取，state 不再背负。
4. **流式进度**：工具层通过 ``runtime.stream_writer`` 推送
   ``review.tool.start`` / ``review.tool.end`` 汇总事件；子图节点内部
   已推送 ``review.<stage>.*`` 详细事件。命名空间与 MinerU
   ``mineru.*``、langextract ``meta.*`` 对齐。
5. **Async 路径**：与 ``metadata.py`` 保持一致，``StructuredTool`` 同时挂
   ``func`` 和 ``coroutine``，调用方可走同步或异步。
"""

from __future__ import annotations

import json
import uuid
from typing import Annotated, Any, Literal

from langchain.tools import ToolRuntime
from langchain_core.tools import InjectedToolArg, StructuredTool
from pydantic import BaseModel, Field, ValidationError

from standard_document_assistant.config import load_config
from standard_document_assistant.graphs.standard_review.graph import get_standard_review_graph
from standard_document_assistant.pathing import resolve_workspace_read_path
from standard_document_assistant.schemas import ReviewIssue, ReviewSummary, ReviewToolResult
from standard_document_assistant.tracing import (
    BUILD_REVIEW_INDEX_TOOL_NAME,
    FORMAT_SOURCE_REVIEW_TOOL_NAME,
    INSPECT_REVIEW_RULES_TOOL_NAME,
    STANDARD_REVIEW_GRAPH_NAME,
    STANDARD_REVIEW_TOOL_NAME,
    VALIDATE_REVIEW_RESULT_TOOL_NAME,
    ainvoke_traced_graph,
    invoke_traced_graph,
)
from standard_document_assistant.review_core.knowledge_base import load_knowledge_base
from standard_document_assistant.review_core.rule_models import RetrievalHit


# ---------------------------------------------------------------------------
# args_schema（与 metadata.py 保持一致风格：BaseModel + Field + description）
# ---------------------------------------------------------------------------


class RunStandardReviewInput(BaseModel):
    """args_schema for :func:`run_standard_review`.

    业务字段全部以 Pydantic 形式声明，由 StructuredTool 入口校验；
    ``runtime`` 由框架通过 ``InjectedToolArg`` 注入，**不**进入 args_schema，
    也**不**进 prompt。
    """

    content_path: str | None = Field(
        default=None,
        description=(
            "workspace 下 Markdown 虚拟路径（如 /workspace/output/mineru/**/*.md）；"
            "与 source_path / manifest_path 至少提供一个。"
        ),
    )
    source_path: str | None = Field(
        default=None,
        description=(
            "workspace 下 PDF/DOCX 虚拟路径；与 content_path / manifest_path 至少提供一个。"
        ),
    )
    manifest_path: str | None = Field(
        default=None,
        description=(
            "workspace 下 MinerU manifest 虚拟路径；提供时优先复用其中列出的产物。"
        ),
    )
    target_scopes: list[str] | None = Field(
        default=None,
        description="限定审核范围（如 ['format', 'content']），不传则使用配置默认。",
    )
    line_start: int | None = Field(
        default=None, description="配合 content_path 时截取起始行（1-based）。"
    )
    line_end: int | None = Field(
        default=None, description="配合 content_path 时截取结束行（1-based，含）。"
    )
    top_k: int | None = Field(
        default=None, description="每条 evidence 召回的规则条数，默认取配置。"
    )
    format_only: bool | None = Field(
        default=None,
        description="True 时仅走格式审核（不调用 LLM），用于 PDF/DOCX 格式问题诊断。",
    )
    output_subdir: str | None = Field(
        default=None,
        description="输出子目录（位于 /workspace/output/reviews/<sub>），未传则随机生成。",
    )
    trace_id: str | None = Field(
        default=None, description="外部 trace 关联 ID；空则由工具自动生成。"
    )
    force_rebuild_index: bool | None = Field(
        default=None, description="是否在审核前强制重建 FAISS 规则索引。"
    )
    partial_mode: str | None = Field(
        default=None,
        description="部分审核模式（sectional / line_range / scopes），默认 sectional。",
    )


class RunFormatSourceReviewInput(BaseModel):
    """args_schema for :func:`run_format_source_review`."""

    source_path: str = Field(
        description="workspace 下 PDF/DOCX 虚拟路径；必填，格式审核的输入源。"
    )
    output_subdir: str | None = Field(
        default=None, description="输出子目录（位于 /workspace/output/reviews/<sub>）。"
    )
    trace_id: str | None = Field(
        default=None, description="外部 trace 关联 ID；空则由工具自动生成。"
    )


class InspectReviewRulesInput(BaseModel):
    """args_schema for :func:`inspect_review_rules`."""

    query: str = Field(
        description="查询关键词或自然语言描述，用于召回相关审核规则。"
    )
    scope: str | None = Field(
        default=None, description="限定 scope（如 'format' / 'content'）。"
    )
    top_k: int | None = Field(
        default=None, description="返回条数上限，默认 5。"
    )
    trace_id: str | None = Field(
        default=None, description="外部 trace 关联 ID；空则由工具自动生成。"
    )


class BuildReviewIndexInput(BaseModel):
    """args_schema for :func:`build_review_index`."""

    trace_id: str | None = Field(
        default=None, description="外部 trace 关联 ID；空则由工具自动生成。"
    )
    force_rebuild: bool | None = Field(
        default=None,
        description="是否丢弃已有索引重新构建，默认 True（推荐在改完 rules_test.md 后调用）。",
    )
    backend: Literal["auto", "faiss", "tfidf_json"] | None = Field(
        default=None,
        description=(
            "索引后端选择：auto=优先 faiss 缺包/缺文件时退到 tfidf_json；"
            "faiss=仅走 faiss-cpu；tfidf_json=仅走纯 Python TF-IDF。"
            "缺省沿用 load_knowledge_base 默认行为。"
        ),
    )


class ValidateReviewResultSchemaInput(BaseModel):
    """args_schema for :func:`validate_review_result_schema`."""

    result_path: str = Field(
        description=(
            "workspace 下 *_audit_result.json 虚拟路径（如 "
            "/workspace/output/reviews/<sub>/xxx_audit_result.json）。"
        )
    )
    trace_id: str | None = Field(
        default=None, description="外部 trace 关联 ID；空则由工具自动生成。"
    )


# ---------------------------------------------------------------------------
# 工具层流式事件（与 metadata._emit_tool_event / parser._emit_event 保持一致）
# ---------------------------------------------------------------------------


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
    """工具层流式事件包装，命名空间 ``review.tool.*``。"""

    if writer is None:
        return
    try:
        writer(event)
    except (RuntimeError, KeyError, AttributeError, TypeError, ValueError):
        return


# ---------------------------------------------------------------------------
# Runtime context 透传到子图（与 metadata._build_runtime_context 对齐）
# ---------------------------------------------------------------------------


def _build_runtime_context(
    *,
    runtime: ToolRuntime | None,
    trace_id: str | None,
    job_id: str | None = None,
) -> dict[str, Any] | None:
    """从 ToolRuntime 抽取横切关注点，组装 ``StandardReviewContext``。

    - ``trace_id``：取自父 agent 的 trace 关联字段（如有），用于子图节点写日志。
    - ``parent_tool_call_id``：取自 tool_call_id，便于 LangSmith 反查。
    - ``job_id``：子图输出物目录，未传则由子图 ``_build_initial_state`` 自生成。
    - 其余字段（tenant_id / user_id）未来由主 agent 在 invoke 时透传。
    """

    if runtime is None:
        if trace_id or job_id:
            ctx: dict[str, Any] = {}
            if trace_id:
                ctx["trace_id"] = trace_id
            if job_id:
                ctx["job_id"] = job_id
            return ctx or None
        return None

    ctx = {}
    raw_ctx = getattr(runtime, "context", None)
    if isinstance(raw_ctx, dict):
        for key in ("tenant_id", "user_id", "trace_id", "job_id"):
            value = raw_ctx.get(key)
            if value is not None and value != "":
                ctx[key] = value
    tool_call_id = getattr(runtime, "tool_call_id", None)
    if tool_call_id:
        ctx["parent_tool_call_id"] = tool_call_id
    configurable = (runtime.config or {}).get("configurable") or {}
    if isinstance(configurable, dict):
        if not ctx.get("trace_id") and configurable.get("thread_id"):
            ctx["trace_id"] = str(configurable["thread_id"])
        if not ctx.get("user_id") and configurable.get("user_id"):
            ctx["user_id"] = str(configurable["user_id"])
    if trace_id and "trace_id" not in ctx:
        ctx["trace_id"] = trace_id
    if job_id and "job_id" not in ctx:
        ctx["job_id"] = job_id
    return ctx or None


# ---------------------------------------------------------------------------
# 状态构造 / 公共结果
# ---------------------------------------------------------------------------


def _build_initial_state(
    *,
    content_path: str | None,
    source_path: str | None,
    manifest_path: str | None,
    target_scopes: list[str] | None,
    line_start: int | None,
    line_end: int | None,
    top_k: int | None,
    format_only: bool,
    output_subdir: str | None,
    trace_id: str | None,
    force_rebuild_index: bool | None,
    partial_mode: str | None,
) -> dict[str, Any]:
    config = load_config()
    job_id = output_subdir or uuid.uuid4().hex[:12]
    return {
        "job_id": job_id,
        "trace_id": trace_id or f"trace_{job_id}",
        "content_path": content_path or "",
        "source_path": source_path or "",
        "manifest_path": manifest_path or "",
        "target_scopes": target_scopes,
        "line_start": line_start,
        "line_end": line_end,
        "top_k": top_k or config.standard_review.top_k,
        "format_only": format_only,
        "output_subdir": output_subdir or job_id,
        "force_rebuild_index": bool(force_rebuild_index) if force_rebuild_index is not None else False,
        "partial_mode": partial_mode or "sectional",
        "issues": [],
        "warnings": [],
        "errors": [],
        "events": [],
        "trace_events": [],
        "status": "success",
    }


def _public_result(result: dict[str, Any]) -> dict[str, Any]:
    output_paths = result.get("output_paths") or {}
    summary = ReviewSummary.model_validate(result.get("aggregate_summary") or {})
    status = "failed" if result.get("errors") else result.get("status", "success")
    payload = ReviewToolResult(
        status="failed" if status == "failed" else "success",
        job_id=result.get("job_id", ""),
        trace_id=result.get("trace_id", ""),
        trace_path=output_paths.get("trace", ""),
        summary=summary,
        artifacts=output_paths,
        warnings=result.get("warnings", []),
        error="; ".join(result.get("errors", [])),
    )
    public = payload.model_dump()
    public["scope_summary"] = result.get("scope_summary") or {}
    public["audit_summary"] = result.get("audit_summary") or {}
    public["retrieval_trace"] = result.get("retrieval_trace") or []
    return public


def _failed_result(
    *,
    message: str,
    errors: list[str] | None = None,
    trace_id: str | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    """构造 handle_tool_errors 等价的失败结果（ReviewToolResult 形式）。"""

    summary = ReviewSummary()
    summary.errors = max(len(errors or [message]), 1)
    payload = ReviewToolResult(
        status="failed",
        job_id=job_id or "",
        trace_id=trace_id or "",
        trace_path="",
        summary=summary,
        artifacts={},
        warnings=[message],
        error=message,
    )
    public = payload.model_dump()
    public["scope_summary"] = {}
    public["audit_summary"] = {}
    public["retrieval_trace"] = []
    if errors:
        public["errors"] = list(errors)
    return public


def _trace_metadata(
    *,
    runtime: ToolRuntime | None,
    content_path: str | None,
    source_path: str | None,
    trace_id: str | None,
) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    if content_path:
        extra["content_path"] = content_path
    if source_path:
        extra["source_path"] = source_path
    if trace_id:
        extra["trace_id"] = trace_id
    if runtime is not None:
        configurable = (runtime.config or {}).get("configurable") or {}
        if isinstance(configurable, dict) and configurable.get("thread_id"):
            extra["thread_id"] = configurable["thread_id"]
    return extra


# ---------------------------------------------------------------------------
# 业务实现（被 sync / async 外壳调用）
# ---------------------------------------------------------------------------


def _dispatch_run_standard_review(
    *,
    content_path: str | None,
    source_path: str | None,
    manifest_path: str | None,
    target_scopes: list[str] | None,
    line_start: int | None,
    line_end: int | None,
    top_k: int | None,
    format_only: bool,
    output_subdir: str | None,
    trace_id: str | None,
    force_rebuild_index: bool | None,
    partial_mode: str | None,
    runtime: ToolRuntime | None,
) -> dict[str, Any]:
    writer = _get_stream_writer(runtime)
    _emit_tool_event(
        {
            "type": "review.tool.start",
            "tool": STANDARD_REVIEW_TOOL_NAME,
            "trace_id": trace_id or "",
        },
        writer=writer,
    )
    if not (content_path or source_path or manifest_path):
        return _failed_result(
            message="content_path / source_path / manifest_path 至少提供一个。",
            trace_id=trace_id,
        )
    state = _build_initial_state(
        content_path=content_path,
        source_path=source_path,
        manifest_path=manifest_path,
        target_scopes=target_scopes,
        line_start=line_start,
        line_end=line_end,
        top_k=top_k,
        format_only=format_only,
        output_subdir=output_subdir,
        trace_id=trace_id,
        force_rebuild_index=force_rebuild_index,
        partial_mode=partial_mode,
    )
    graph = get_standard_review_graph()
    parent_config = runtime.config if runtime is not None else None
    tool_call_id = runtime.tool_call_id if runtime is not None else None
    context = _build_runtime_context(
        runtime=runtime,
        trace_id=trace_id,
        job_id=state.get("job_id"),
    )
    try:
        result = invoke_traced_graph(
            graph,
            state,
            parent_config=parent_config,
            graph_name=STANDARD_REVIEW_GRAPH_NAME,
            tool_name=STANDARD_REVIEW_TOOL_NAME,
            tool_call_id=tool_call_id,
            extra_metadata=_trace_metadata(
                runtime=runtime,
                content_path=content_path,
                source_path=source_path,
                trace_id=trace_id,
            ),
            context=context,
        )
    except Exception as exc:
        return _failed_result(
            message=f"run_standard_review 执行失败：{exc}",
            errors=[str(exc)],
            trace_id=trace_id,
            job_id=state.get("job_id"),
        )
    public = _public_result(result)
    _emit_tool_event(
        {
            "type": "review.tool.end",
            "tool": STANDARD_REVIEW_TOOL_NAME,
            "trace_id": trace_id or public.get("trace_id", ""),
            "status": public.get("status", "success"),
        },
        writer=writer,
    )
    return public


async def _adispatch_run_standard_review(
    *,
    content_path: str | None,
    source_path: str | None,
    manifest_path: str | None,
    target_scopes: list[str] | None,
    line_start: int | None,
    line_end: int | None,
    top_k: int | None,
    format_only: bool,
    output_subdir: str | None,
    trace_id: str | None,
    force_rebuild_index: bool | None,
    partial_mode: str | None,
    runtime: ToolRuntime | None,
) -> dict[str, Any]:
    writer = _get_stream_writer(runtime)
    _emit_tool_event(
        {
            "type": "review.tool.start",
            "tool": STANDARD_REVIEW_TOOL_NAME,
            "trace_id": trace_id or "",
        },
        writer=writer,
    )
    if not (content_path or source_path or manifest_path):
        return _failed_result(
            message="content_path / source_path / manifest_path 至少提供一个。",
            trace_id=trace_id,
        )
    state = _build_initial_state(
        content_path=content_path,
        source_path=source_path,
        manifest_path=manifest_path,
        target_scopes=target_scopes,
        line_start=line_start,
        line_end=line_end,
        top_k=top_k,
        format_only=format_only,
        output_subdir=output_subdir,
        trace_id=trace_id,
        force_rebuild_index=force_rebuild_index,
        partial_mode=partial_mode,
    )
    graph = get_standard_review_graph()
    parent_config = runtime.config if runtime is not None else None
    tool_call_id = runtime.tool_call_id if runtime is not None else None
    context = _build_runtime_context(
        runtime=runtime,
        trace_id=trace_id,
        job_id=state.get("job_id"),
    )
    try:
        result = await ainvoke_traced_graph(
            graph,
            state,
            parent_config=parent_config,
            graph_name=STANDARD_REVIEW_GRAPH_NAME,
            tool_name=STANDARD_REVIEW_TOOL_NAME,
            tool_call_id=tool_call_id,
            extra_metadata=_trace_metadata(
                runtime=runtime,
                content_path=content_path,
                source_path=source_path,
                trace_id=trace_id,
            ),
            context=context,
        )
    except Exception as exc:
        return _failed_result(
            message=f"run_standard_review 执行失败：{exc}",
            errors=[str(exc)],
            trace_id=trace_id,
            job_id=state.get("job_id"),
        )
    public = _public_result(result)
    _emit_tool_event(
        {
            "type": "review.tool.end",
            "tool": STANDARD_REVIEW_TOOL_NAME,
            "trace_id": trace_id or public.get("trace_id", ""),
            "status": public.get("status", "success"),
        },
        writer=writer,
    )
    return public


def _dispatch_run_format_source_review(
    *,
    source_path: str,
    output_subdir: str | None,
    trace_id: str | None,
    runtime: ToolRuntime | None,
) -> dict[str, Any]:
    return _dispatch_run_standard_review(
        content_path=None,
        source_path=source_path,
        manifest_path=None,
        target_scopes=["format"],
        line_start=None,
        line_end=None,
        top_k=None,
        format_only=True,
        output_subdir=output_subdir,
        trace_id=trace_id,
        force_rebuild_index=False,
        partial_mode="format",
        runtime=runtime,
    )


async def _adispatch_run_format_source_review(
    *,
    source_path: str,
    output_subdir: str | None,
    trace_id: str | None,
    runtime: ToolRuntime | None,
) -> dict[str, Any]:
    return await _adispatch_run_standard_review(
        content_path=None,
        source_path=source_path,
        manifest_path=None,
        target_scopes=["format"],
        line_start=None,
        line_end=None,
        top_k=None,
        format_only=True,
        output_subdir=output_subdir,
        trace_id=trace_id,
        force_rebuild_index=False,
        partial_mode="format",
        runtime=runtime,
    )


def _dispatch_inspect_review_rules(
    *,
    query: str,
    scope: str | None,
    top_k: int,
    trace_id: str | None,
    runtime: ToolRuntime | None,
) -> dict[str, Any]:
    writer = _get_stream_writer(runtime)
    _emit_tool_event(
        {
            "type": "review.tool.start",
            "tool": INSPECT_REVIEW_RULES_TOOL_NAME,
            "trace_id": trace_id or "",
        },
        writer=writer,
    )
    if not query or not query.strip():
        return {
            "status": "failed",
            "trace_id": trace_id or "",
            "error": "query 不能为空。",
            "matches": [],
        }
    config = load_config().standard_review
    try:
        kb, kb_meta = load_knowledge_base(config)
    except Exception as exc:
        _emit_tool_event(
            {
                "type": "review.tool.end",
                "tool": INSPECT_REVIEW_RULES_TOOL_NAME,
                "status": "failed",
            },
            writer=writer,
        )
        return {
            "status": "failed",
            "trace_id": trace_id or "",
            "error": f"知识库加载失败：{exc}",
            "matches": [],
        }
    hits: list[RetrievalHit] = []
    if kb.index is not None:
        try:
            hits = kb.search(query, scope=scope, top_k=top_k)
        except Exception:
            hits = []
    if not hits:
        query_terms = {token for token in query.lower().split() if token}
        for rule in kb.rules:
            if scope and rule.scope != scope:
                continue
            haystack = f"{rule.title}\n{rule.content}\n{rule.scope}".lower()
            if query and any(term in haystack for term in query_terms):
                hits.append(
                    RetrievalHit(rule=rule, score=0.5, source="keyword", vector_score=0.0)
                )
    matches = [
        {
            "chunk_id": hit.rule.chunk_id,
            "rule_id": hit.rule.chunk_id,
            "rule_name": hit.rule.title,
            "scope": hit.rule.scope,
            "text": hit.rule.content,
            "source_ref": hit.rule.source_ref,
            "tags": list(hit.rule.tags),
            "analysis_mode": hit.rule.analysis_mode,
            "target_scopes": list(hit.rule.target_scopes),
            "score": hit.score,
            "source": hit.source,
        }
        for hit in hits[:top_k]
    ]
    payload = {
        "status": "ok",
        "trace_id": trace_id or "",
        "rules_metadata": kb_meta,
        "matches": matches,
    }
    _emit_tool_event(
        {
            "type": "review.tool.end",
            "tool": INSPECT_REVIEW_RULES_TOOL_NAME,
            "status": "ok",
        },
        writer=writer,
    )
    return payload


def _dispatch_build_review_index(
    *,
    trace_id: str | None,
    force_rebuild: bool,
    backend: str | None = None,
    runtime: ToolRuntime | None,
) -> dict[str, Any]:
    writer = _get_stream_writer(runtime)
    _emit_tool_event(
        {
            "type": "review.tool.start",
            "tool": BUILD_REVIEW_INDEX_TOOL_NAME,
            "trace_id": trace_id or "",
        },
        writer=writer,
    )
    config = load_config().standard_review
    try:
        kwargs: dict[str, Any] = {"force_rebuild": force_rebuild}
        if backend:
            kwargs["backend"] = backend
        kb, kb_meta = load_knowledge_base(config, **kwargs)
    except Exception as exc:
        _emit_tool_event(
            {
                "type": "review.tool.end",
                "tool": BUILD_REVIEW_INDEX_TOOL_NAME,
                "status": "failed",
            },
            writer=writer,
        )
        return {
            "status": "failed",
            "trace_id": trace_id or "",
            "error": f"索引构建失败：{exc}",
        }
    payload = {
        "status": "ok",
        "trace_id": trace_id or "",
        "rules_metadata": kb_meta,
        "rules_loaded": len(kb.rules),
        "index_source": kb_meta.get("index_source", "rebuilt"),
        "index_backend": kb_meta.get("index_backend", "unknown"),
    }
    warnings = kb_meta.get("warnings") or []
    if warnings:
        payload["warnings"] = list(warnings)
    _emit_tool_event(
        {
            "type": "review.tool.end",
            "tool": BUILD_REVIEW_INDEX_TOOL_NAME,
            "status": "ok",
            "index_backend": payload["index_backend"],
        },
        writer=writer,
    )
    return payload


def _dispatch_validate_review_result_schema(
    *,
    result_path: str,
    trace_id: str | None,
    runtime: ToolRuntime | None,
) -> dict[str, Any]:
    writer = _get_stream_writer(runtime)
    _emit_tool_event(
        {
            "type": "review.tool.start",
            "tool": VALIDATE_REVIEW_RESULT_TOOL_NAME,
            "trace_id": trace_id or "",
        },
        writer=writer,
    )
    if not result_path or not result_path.strip():
        _emit_tool_event(
            {
                "type": "review.tool.end",
                "tool": VALIDATE_REVIEW_RESULT_TOOL_NAME,
                "status": "failed",
            },
            writer=writer,
        )
        return {
            "valid": False,
            "trace_id": trace_id or "",
            "errors": ["result_path 不能为空。"],
        }
    try:
        path, virtual = resolve_workspace_read_path(result_path, suffixes={".json"})
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError) as exc:
        _emit_tool_event(
            {
                "type": "review.tool.end",
                "tool": VALIDATE_REVIEW_RESULT_TOOL_NAME,
                "status": "failed",
            },
            writer=writer,
        )
        return {
            "valid": False,
            "trace_id": trace_id or "",
            "result_path": result_path,
            "errors": [f"读取结果文件失败：{exc}"],
        }

    errors: list[str] = []
    try:
        summary = ReviewSummary.model_validate(data.get("summary") or {})
    except ValidationError as exc:
        errors.append(f"summary schema error: {exc}")
        summary = ReviewSummary()
    for idx, item in enumerate(data.get("issues") or [], start=1):
        try:
            ReviewIssue.model_validate(item)
        except ValidationError as exc:
            errors.append(f"issue[{idx}] schema error: {exc}")
    artifacts = data.get("artifacts") or {}
    if isinstance(artifacts, dict):
        for key in ["report", "result", "trace", "manifest"]:
            if key in artifacts and not str(artifacts[key]).startswith("/workspace/"):
                errors.append(f"artifact {key} 不是 /workspace/ 虚拟路径。")
    scope_summary = data.get("scope_summary") or {}
    if scope_summary and not isinstance(scope_summary, dict):
        errors.append("scope_summary 必须是 dict。")
    audit_summary = data.get("audit_summary") or {}
    if audit_summary and not isinstance(audit_summary, dict):
        errors.append("audit_summary 必须是 dict。")
    payload = {
        "valid": not errors,
        "trace_id": trace_id or data.get("trace_id", ""),
        "result_path": virtual,
        "summary": summary.model_dump(),
        "errors": errors,
    }
    _emit_tool_event(
        {
            "type": "review.tool.end",
            "tool": VALIDATE_REVIEW_RESULT_TOOL_NAME,
            "status": "ok" if payload["valid"] else "failed",
        },
        writer=writer,
    )
    return payload


# ---------------------------------------------------------------------------
# StructuredTool 入口：args_schema 校验 + ToolRuntime 注入
# ---------------------------------------------------------------------------


def _run_standard_review_sync(
    content_path: str | None = None,
    *,
    source_path: str | None = None,
    manifest_path: str | None = None,
    target_scopes: list[str] | None = None,
    line_start: int | None = None,
    line_end: int | None = None,
    top_k: int | None = None,
    format_only: bool | None = None,
    output_subdir: str | None = None,
    trace_id: str | None = None,
    force_rebuild_index: bool | None = None,
    partial_mode: str | None = None,
    runtime: Annotated[ToolRuntime | None, InjectedToolArg] = None,
) -> dict[str, Any]:
    return _dispatch_run_standard_review(
        content_path=content_path,
        source_path=source_path,
        manifest_path=manifest_path,
        target_scopes=target_scopes,
        line_start=line_start,
        line_end=line_end,
        top_k=top_k,
        format_only=bool(format_only) if format_only is not None else False,
        output_subdir=output_subdir,
        trace_id=trace_id,
        force_rebuild_index=force_rebuild_index,
        partial_mode=partial_mode,
        runtime=runtime,
    )


async def _arun_standard_review_sync(
    content_path: str | None = None,
    *,
    source_path: str | None = None,
    manifest_path: str | None = None,
    target_scopes: list[str] | None = None,
    line_start: int | None = None,
    line_end: int | None = None,
    top_k: int | None = None,
    format_only: bool | None = None,
    output_subdir: str | None = None,
    trace_id: str | None = None,
    force_rebuild_index: bool | None = None,
    partial_mode: str | None = None,
    runtime: Annotated[ToolRuntime | None, InjectedToolArg] = None,
) -> dict[str, Any]:
    return await _adispatch_run_standard_review(
        content_path=content_path,
        source_path=source_path,
        manifest_path=manifest_path,
        target_scopes=target_scopes,
        line_start=line_start,
        line_end=line_end,
        top_k=top_k,
        format_only=bool(format_only) if format_only is not None else False,
        output_subdir=output_subdir,
        trace_id=trace_id,
        force_rebuild_index=force_rebuild_index,
        partial_mode=partial_mode,
        runtime=runtime,
    )


def _run_format_source_review_sync(
    source_path: str,
    *,
    output_subdir: str | None = None,
    trace_id: str | None = None,
    runtime: Annotated[ToolRuntime | None, InjectedToolArg] = None,
) -> dict[str, Any]:
    return _dispatch_run_format_source_review(
        source_path=source_path,
        output_subdir=output_subdir,
        trace_id=trace_id,
        runtime=runtime,
    )


async def _arun_format_source_review_sync(
    source_path: str,
    *,
    output_subdir: str | None = None,
    trace_id: str | None = None,
    runtime: Annotated[ToolRuntime | None, InjectedToolArg] = None,
) -> dict[str, Any]:
    return await _adispatch_run_format_source_review(
        source_path=source_path,
        output_subdir=output_subdir,
        trace_id=trace_id,
        runtime=runtime,
    )


def _inspect_review_rules_sync(
    query: str,
    *,
    scope: str | None = None,
    top_k: int | None = None,
    trace_id: str | None = None,
    runtime: Annotated[ToolRuntime | None, InjectedToolArg] = None,
) -> dict[str, Any]:
    return _dispatch_inspect_review_rules(
        query=query,
        scope=scope,
        top_k=top_k if top_k is not None else 5,
        trace_id=trace_id,
        runtime=runtime,
    )


async def _ainspect_review_rules_sync(
    query: str,
    *,
    scope: str | None = None,
    top_k: int | None = None,
    trace_id: str | None = None,
    runtime: Annotated[ToolRuntime | None, InjectedToolArg] = None,
) -> dict[str, Any]:
    # 知识库检索本地完成，async / sync 行为一致
    return _dispatch_inspect_review_rules(
        query=query,
        scope=scope,
        top_k=top_k if top_k is not None else 5,
        trace_id=trace_id,
        runtime=runtime,
    )


def _build_review_index_sync(
    *,
    trace_id: str | None = None,
    force_rebuild: bool | None = None,
    backend: str | None = None,
    runtime: Annotated[ToolRuntime | None, InjectedToolArg] = None,
) -> dict[str, Any]:
    return _dispatch_build_review_index(
        trace_id=trace_id,
        force_rebuild=bool(force_rebuild) if force_rebuild is not None else True,
        backend=backend,
        runtime=runtime,
    )


async def _abuild_review_index_sync(
    *,
    trace_id: str | None = None,
    force_rebuild: bool | None = None,
    backend: str | None = None,
    runtime: Annotated[ToolRuntime | None, InjectedToolArg] = None,
) -> dict[str, Any]:
    return _dispatch_build_review_index(
        trace_id=trace_id,
        force_rebuild=bool(force_rebuild) if force_rebuild is not None else True,
        backend=backend,
        runtime=runtime,
    )


def _validate_review_result_schema_sync(
    result_path: str,
    *,
    trace_id: str | None = None,
    runtime: Annotated[ToolRuntime | None, InjectedToolArg] = None,
) -> dict[str, Any]:
    return _dispatch_validate_review_result_schema(
        result_path=result_path,
        trace_id=trace_id,
        runtime=runtime,
    )


async def _avalidate_review_result_schema_sync(
    result_path: str,
    *,
    trace_id: str | None = None,
    runtime: Annotated[ToolRuntime | None, InjectedToolArg] = None,
) -> dict[str, Any]:
    return _dispatch_validate_review_result_schema(
        result_path=result_path,
        trace_id=trace_id,
        runtime=runtime,
    )


run_standard_review = StructuredTool.from_function(
    func=_run_standard_review_sync,
    coroutine=_arun_standard_review_sync,
    name=STANDARD_REVIEW_TOOL_NAME,
    description=(
        "Run standard document review from MinerU Markdown, optional source file, "
        "or MinerU manifest. Uses FAISS RAG + LLM Judge (multi-strategy) for the "
        "content track and deterministic DOCX/PDF checks for the format track. "
        "Writes report/result/trace/manifest under /workspace/output/reviews."
    ),
    args_schema=RunStandardReviewInput,
)

run_format_source_review = StructuredTool.from_function(
    func=_run_format_source_review_sync,
    coroutine=_arun_format_source_review_sync,
    name=FORMAT_SOURCE_REVIEW_TOOL_NAME,
    description="Run format-source review for a PDF/DOCX source and persist review artifacts.",
    args_schema=RunFormatSourceReviewInput,
)

inspect_review_rules = StructuredTool.from_function(
    func=_inspect_review_rules_sync,
    coroutine=_ainspect_review_rules_sync,
    name=INSPECT_REVIEW_RULES_TOOL_NAME,
    description=(
        "Inspect review rules by query and scope using the FAISS/TF-IDF knowledge base. "
        "Use this to preview which rules will be evaluated for a given scope before "
        "running the full review."
    ),
    args_schema=InspectReviewRulesInput,
)

build_review_index = StructuredTool.from_function(
    func=_build_review_index_sync,
    coroutine=_abuild_review_index_sync,
    name=BUILD_REVIEW_INDEX_TOOL_NAME,
    description=(
        "Build (or rebuild) the FAISS/TF-IDF review-rules vector index from the "
        "configured rules markdown. Required after editing rules_test.md or when "
        "switching the embedding model."
    ),
    args_schema=BuildReviewIndexInput,
)

validate_review_result_schema = StructuredTool.from_function(
    func=_validate_review_result_schema_sync,
    coroutine=_avalidate_review_result_schema_sync,
    name=VALIDATE_REVIEW_RESULT_TOOL_NAME,
    description="Validate a standard review *_audit_result.json schema and artifact references.",
    args_schema=ValidateReviewResultSchemaInput,
)
