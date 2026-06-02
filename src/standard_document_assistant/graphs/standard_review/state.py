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
