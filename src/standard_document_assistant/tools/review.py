"""Standard review tools."""

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
from standard_document_assistant.review_core.rules import load_review_rules
from standard_document_assistant.schemas import ReviewIssue, ReviewSummary, ReviewToolResult
from standard_document_assistant.tracing import (
    FORMAT_SOURCE_REVIEW_TOOL_NAME,
    INSPECT_REVIEW_RULES_TOOL_NAME,
    STANDARD_REVIEW_GRAPH_NAME,
    STANDARD_REVIEW_TOOL_NAME,
    VALIDATE_REVIEW_RESULT_TOOL_NAME,
    invoke_traced_graph,
)


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
    return payload.model_dump()


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
    runtime: Annotated[ToolRuntime | None, InjectedToolArg] = None,
) -> dict[str, Any]:
    """Run the standard review LangGraph and persist report/result/trace/manifest."""

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
    rules, metadata = load_review_rules()
    query_terms = {item for item in query.lower().split() if item}
    matches = []
    for rule in rules:
        if scope and rule.get("scope") != scope:
            continue
        haystack = f"{rule.get('rule_name', '')}\n{rule.get('text', '')}".lower()
        score = sum(1 for term in query_terms if term in haystack)
        if query and query in haystack:
            score += 3
        matches.append({**rule, "score": score})
    matches.sort(key=lambda item: item.get("score", 0), reverse=True)
    return {
        "status": "ok",
        "trace_id": trace_id or "",
        "rules_metadata": metadata,
        "matches": matches[:top_k],
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
        "or MinerU manifest. Writes report/result/trace/manifest under /workspace/output/reviews."
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
    description="Inspect review rules by query and scope without making formal audit judgments.",
)

validate_review_result_schema = StructuredTool.from_function(
    func=_validate_review_result_schema_sync,
    name=VALIDATE_REVIEW_RESULT_TOOL_NAME,
    description="Validate a standard review *_audit_result.json schema and artifact references.",
)
