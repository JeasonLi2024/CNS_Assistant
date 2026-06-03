"""State schema for the standard review graph (Deep Agents integration)."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class StandardReviewState(TypedDict, total=False):
    job_id: str
    trace_id: str
    content_path: str
    source_path: str
    manifest_path: str
    target_scopes: list[str] | None
    line_start: int | None
    line_end: int | None
    top_k: int
    format_only: bool
    output_subdir: str
    force_rebuild_index: bool
    partial_mode: str

    parsed_document: dict[str, Any]
    scope_text_map: dict[str, str]
    active_scope_keys: list[str]
    format_document: dict[str, Any] | None
    format_facts: dict[str, Any] | None

    section_rules: list[dict[str, Any]]
    full_document_rules: list[dict[str, Any]]
    retrieval_trace: list[dict[str, Any]]
    rules_metadata: dict[str, Any]
    section_rule_objects: list[dict[str, Any]]
    full_document_rule_objects: list[dict[str, Any]]

    issues: Annotated[list[dict[str, Any]], operator.add]
    warnings: Annotated[list[str], operator.add]
    errors: Annotated[list[str], operator.add]
    events: Annotated[list[dict[str, Any]], operator.add]
    trace_events: Annotated[list[dict[str, Any]], operator.add]

    scope_summary: dict[str, dict[str, Any]]
    audit_summary: dict[str, Any]
    aggregate_summary: dict[str, Any]
    report_markdown: str
    result_payload: dict[str, Any]
    trace_payload: dict[str, Any]
    output_paths: dict[str, str]

    review_round: int
    max_review_rounds: int
    insufficient_scopes: list[str]
    widened: bool
    final_status: str
    status: str


class StandardReviewContext(TypedDict, total=False):
    """Runtime context for the standard review subgraph (Deep Agents).

    节点通过 ``Runtime[StandardReviewContext]`` 读取横切关注点，避免污染
    graph state。依据 LangGraph Runtime context 文档：
    https://docs.langchain.com/oss/python/langgraph/graph-api#runtime-context

    - ``trace_id``：父 agent 透传的 trace 关联 ID。
    - ``tenant_id`` / ``user_id``：多租户隔离字段，未来由主 agent 透传。
    - ``job_id``：子图输出物目录键。
    - ``quality_strict``：是否启用更严格的复核（影响 quality_gate 阈值）。
    - ``parent_tool_call_id``：父 agent 的 tool_call_id，便于 LangSmith 反查。
    """

    trace_id: str
    tenant_id: str
    user_id: str
    job_id: str
    quality_strict: bool
    parent_tool_call_id: str
