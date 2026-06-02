"""Standard review tools (Deep Agents integration)."""

from __future__ import annotations

import json
import uuid
from typing import Annotated, Any

from langchain.tools import ToolRuntime
from langchain_core.tools import InjectedToolArg, StructuredTool
from pydantic import ValidationError

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
    invoke_traced_graph,
)
from standard_document_assistant.review_core.knowledge_base import load_knowledge_base
from standard_document_assistant.review_core.rule_models import RetrievalHit


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


def _run_standard_review_sync(
    content_path: str | None = None,
    *,
    source_path: str | None = None,
    manifest_path: str | None = None,
    target_scopes: list[str] | None = None,
    line_start: int | None = None,
    line_end: int | None = None,
    top_k: int | None = None,
    format_only: bool = False,
    output_subdir: str | None = None,
    trace_id: str | None = None,
    force_rebuild_index: bool | None = None,
    partial_mode: str | None = None,
    runtime: Annotated[ToolRuntime | None, InjectedToolArg] = None,
) -> dict[str, Any]:
    """Run the standard review graph and persist report/result/trace/manifest."""

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
    )
    return _public_result(result)


def _run_format_source_review_sync(
    source_path: str,
    *,
    output_subdir: str | None = None,
    trace_id: str | None = None,
    runtime: Annotated[ToolRuntime | None, InjectedToolArg] = None,
) -> dict[str, Any]:
    return _run_standard_review_sync(
        source_path=source_path,
        format_only=True,
        output_subdir=output_subdir,
        trace_id=trace_id,
        runtime=runtime,
    )


def _inspect_review_rules_sync(
    query: str,
    *,
    scope: str | None = None,
    top_k: int = 5,
    trace_id: str | None = None,
) -> dict[str, Any]:
    """Inspect review rules using the FAISS/TF-IDF knowledge base.

    Falls back to a keyword ranker if the index is unavailable so the tool
    remains useful in low-resource environments.
    """

    config = load_config().standard_review
    try:
        kb, kb_meta = load_knowledge_base(config)
    except Exception as exc:
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
                hits.append(RetrievalHit(rule=rule, score=0.5, source="keyword", vector_score=0.0))
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
    return {
        "status": "ok",
        "trace_id": trace_id or "",
        "rules_metadata": kb_meta,
        "matches": matches,
    }


def _build_review_index_sync(
    *,
    trace_id: str | None = None,
    force_rebuild: bool = True,
) -> dict[str, Any]:
    config = load_config().standard_review
    try:
        kb, kb_meta = load_knowledge_base(config, force_rebuild=force_rebuild)
    except Exception as exc:
        return {
            "status": "failed",
            "trace_id": trace_id or "",
            "error": f"索引构建失败：{exc}",
        }
    return {
        "status": "ok",
        "trace_id": trace_id or "",
        "rules_metadata": kb_meta,
        "rules_loaded": len(kb.rules),
        "index_source": kb_meta.get("index_source", "rebuilt"),
    }


def _validate_review_result_schema_sync(
    result_path: str,
    *,
    trace_id: str | None = None,
) -> dict[str, Any]:
    path, virtual = resolve_workspace_read_path(result_path, suffixes={".json"})
    data = json.loads(path.read_text(encoding="utf-8"))
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
    return {
        "valid": not errors,
        "trace_id": trace_id or data.get("trace_id", ""),
        "result_path": virtual,
        "summary": summary.model_dump(),
        "errors": errors,
    }


run_standard_review = StructuredTool.from_function(
    func=_run_standard_review_sync,
    name=STANDARD_REVIEW_TOOL_NAME,
    description=(
        "Run standard document review from MinerU Markdown, optional source file, "
        "or MinerU manifest. Uses FAISS RAG + LLM Judge (multi-strategy) for the "
        "content track and deterministic DOCX/PDF checks for the format track. "
        "Writes report/result/trace/manifest under /workspace/output/reviews."
    ),
)

run_format_source_review = StructuredTool.from_function(
    func=_run_format_source_review_sync,
    name=FORMAT_SOURCE_REVIEW_TOOL_NAME,
    description="Run format-source review for a PDF/DOCX source and persist review artifacts.",
)

inspect_review_rules = StructuredTool.from_function(
    func=_inspect_review_rules_sync,
    name=INSPECT_REVIEW_RULES_TOOL_NAME,
    description=(
        "Inspect review rules by query and scope using the FAISS/TF-IDF knowledge base. "
        "Use this to preview which rules will be evaluated for a given scope before "
        "running the full review."
    ),
)

build_review_index = StructuredTool.from_function(
    func=_build_review_index_sync,
    name=BUILD_REVIEW_INDEX_TOOL_NAME,
    description=(
        "Build (or rebuild) the FAISS/TF-IDF review-rules vector index from the "
        "configured rules markdown. Required after editing rules_test.md or when "
        "switching the embedding model."
    ),
)

validate_review_result_schema = StructuredTool.from_function(
    func=_validate_review_result_schema_sync,
    name=VALIDATE_REVIEW_RESULT_TOOL_NAME,
    description="Validate a standard review *_audit_result.json schema and artifact references.",
)
