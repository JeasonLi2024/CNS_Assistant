"""Report subgraph: write outputs (result/trace/report/manifest) + LLM audit summary.

Stream events (2026-06-03 rev. 3): uses shared ``emit_event`` helper to
both append to ``state["trace_events"]`` and push ``review.report.*`` /
``review.manifest.*`` via ``get_stream_writer`` for unified
``<domain>.<stage>`` namespace.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from standard_document_assistant.config import load_config
from standard_document_assistant.constants import REVIEWS_OUTPUT_DIR
from standard_document_assistant.graphs.standard_review.events import emit_event
from standard_document_assistant.graphs.standard_review.state import StandardReviewState
from standard_document_assistant.pathing import (
    allocate_unique_path,
    host_to_virtual_path,
    review_output_root,
    safe_name,
    utc_now_iso,
    write_json,
)
from standard_document_assistant.review_core.audit_summary import generate_audit_summary
from standard_document_assistant.review_core.reporter import render_markdown_report
from standard_document_assistant.review_core.rule_models import AuditIssue, AuditResult
from standard_document_assistant.schemas import ArtifactManifest, ArtifactRef
from standard_document_assistant.tracing import STANDARD_REVIEW_TOOL_NAME


def write_outputs(state: StandardReviewState) -> dict[str, Any]:
    output_dir = review_output_root(state.get("output_subdir") or state.get("job_id") or "")
    stem = safe_name(Path(state.get("content_path") or "standard").stem, fallback="standard")
    report_path = allocate_unique_path(output_dir, f"{stem}_audit_report", ".md")
    result_path = allocate_unique_path(output_dir, f"{stem}_audit_result", ".json")
    trace_path = allocate_unique_path(output_dir, f"{stem}_audit_trace", ".json")

    issues_raw = state.get("issues") or []
    issues = [_to_audit_issue(item) for item in issues_raw]
    audit_result = AuditResult(file_name=state.get("content_path", ""))
    for issue in issues:
        audit_result.add_issue(issue)

    audit_summary = _maybe_audit_summary(issues, file_name=state.get("content_path", "") or "standard")

    result_payload = {
        "status": state.get("status", "success"),
        "job_id": state.get("job_id", ""),
        "trace_id": state.get("trace_id", ""),
        "inputs": {
            "content_path": state.get("content_path", ""),
            "source_path": state.get("source_path", ""),
            "manifest_path": state.get("manifest_path", ""),
        },
        "summary": state.get("aggregate_summary") or {},
        "scope_summary": state.get("scope_summary") or {},
        "audit_summary": audit_summary.to_dict() if audit_summary else {},
        "issues": [issue.to_dict() for issue in issues],
        "warnings": state.get("warnings") or [],
        "rules": state.get("rules_metadata") or {},
        "retrieval_trace": state.get("retrieval_trace") or [],
        "format_trace": state.get("format_facts") or {},
        "created_at": utc_now_iso(),
    }
    trace_payload = {
        "trace_id": state.get("trace_id", ""),
        "job_id": state.get("job_id", ""),
        "component": "standard_review_graph",
        "events": state.get("trace_events") or [],
        "warnings": state.get("warnings") or [],
        "review_round": state.get("review_round", 0),
        "max_review_rounds": state.get("max_review_rounds", 0),
        "widened": state.get("widened", False),
        "partial_mode": state.get("partial_mode", "sectional"),
    }
    report_markdown = render_markdown_report(result_payload)
    report_path.write_text(report_markdown, encoding="utf-8")
    write_json(result_path, result_payload)
    write_json(trace_path, trace_payload)
    paths = {
        "report": host_to_virtual_path(report_path),
        "result": host_to_virtual_path(result_path),
        "trace": host_to_virtual_path(trace_path),
    }
    return {
        "report_markdown": report_markdown,
        "result_payload": result_payload,
        "trace_payload": trace_payload,
        "audit_summary": audit_summary.to_dict() if audit_summary else {},
        "output_paths": paths,
        "trace_events": [emit_event(state, "write_outputs", "success")],
    }


def write_manifest(
    state: StandardReviewState,
    runtime: Any = None,
) -> dict[str, Any]:
    output_paths = dict(state.get("output_paths") or {})
    output_dir = REVIEWS_OUTPUT_DIR / safe_name(state.get("output_subdir") or state.get("job_id") or "")
    stem = safe_name(Path(state.get("content_path") or "standard").stem, fallback="standard")
    manifest_path = allocate_unique_path(output_dir, f"{stem}_review_manifest", ".json")
    artifacts = [
        ArtifactRef(type=key, virtual_path=value, description=f"标准审核 {key}")
        for key, value in output_paths.items()
    ]
    primary = next((item for item in artifacts if item.type == "report"), None)
    manifest = ArtifactManifest(
        tool=STANDARD_REVIEW_TOOL_NAME,
        status="ok" if state.get("status", "success") == "success" else "failed",
        source_virtual_path=state.get("content_path", ""),
        primary_artifact=primary,
        artifacts=artifacts,
        warnings=state.get("warnings") or [],
        error="; ".join(state.get("errors") or []),
        created_at=utc_now_iso(),
    )
    payload = manifest.model_dump()
    payload["trace_id"] = state.get("trace_id", "")
    payload["job_id"] = state.get("job_id", "")
    payload["inputs"] = {
        "content_path": state.get("content_path", ""),
        "source_path": state.get("source_path", ""),
        "manifest_path": state.get("manifest_path", ""),
    }
    payload["rules"] = state.get("rules_metadata") or {}
    payload["audit_summary"] = state.get("audit_summary") or {}
    payload["scope_summary"] = state.get("scope_summary") or {}
    write_json(manifest_path, payload)
    output_paths["manifest"] = host_to_virtual_path(manifest_path)
    return {
        "output_paths": output_paths,
        "trace_events": [emit_event(state, "write_manifest", "success")],
    }


def _to_audit_issue(payload: dict[str, Any]) -> AuditIssue:
    extras = dict(payload.get("extras") or {})
    severity = _normalize_severity(payload.get("severity"))
    status = _normalize_status(payload.get("status"))
    return AuditIssue(
        issue_id=str(payload.get("issue_id") or ""),
        file_name=str(payload.get("file_name") or ""),
        rule_id=str(payload.get("rule_id") or ""),
        rule_name=str(payload.get("rule_name") or ""),
        scope=str(payload.get("scope") or ""),
        severity=severity,
        status=status,
        expected=str(payload.get("expected") or ""),
        actual=str(payload.get("actual") or ""),
        evidence_text=str(payload.get("evidence_text") or ""),
        source_ref=str(payload.get("source_ref") or ""),
        suggestion=str(payload.get("suggestion") or ""),
        confidence=float(payload.get("confidence") or 0.0),
        llm_reasoning=str(payload.get("llm_reasoning") or ""),
        extras={**extras, "audit_track": payload.get("audit_track") or extras.get("audit_track") or "content_llm"},
    )


_SEVERITY_MAP = {
    "致命": "critical",
    "严重": "critical",
    "重度": "critical",
    "中度": "major",
    "轻度": "minor",
    "提示": "info",
    "warn": "minor",
}


def _normalize_severity(value: Any) -> str:
    if value is None:
        return "info"
    text = str(value).strip()
    if text in {"critical", "major", "minor", "info"}:
        return text
    return _SEVERITY_MAP.get(text, "info")


_STATUS_MAP = {
    "pass": "pass",
    "fail": "fail",
    "warn": "warn",
    "insufficient_context": "insufficient_context",
    "llm_error": "fail",
    "not_ready": "insufficient_context",
    "info": "warn",
}


def _normalize_status(value: Any) -> str:
    if value is None:
        return "info"
    text = str(value).strip()
    if text in _STATUS_MAP:
        return _STATUS_MAP[text]
    return "warn"


def _maybe_audit_summary(issues: list[AuditIssue], *, file_name: str):
    config = load_config().standard_review
    if not config.enable_audit_summary:
        return None
    try:
        return generate_audit_summary(issues, file_name=file_name, config=config)
    except Exception:
        return None


# 旧 _event 辅助函数已迁移至 ``standard_document_assistant.graphs.standard_review.events.emit_event``。
# 旧 _event 仅写 state["trace_events"]；新 emit_event 既写 state["trace_events"]，也通过
# get_stream_writer 推送 ``review.report.*`` / ``review.manifest.*`` 事件，与 MinerU
# ``mineru.*``、langextract ``meta.*`` 形成统一 ``<domain>.<stage>`` 命名空间
# （2026-06-03 rev. 3）。
