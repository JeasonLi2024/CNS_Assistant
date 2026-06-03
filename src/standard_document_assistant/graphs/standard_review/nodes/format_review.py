"""Format review subgraph (deterministic DOCX/PDF checks).

Stream events (2026-06-03 rev. 3): uses shared ``emit_event`` helper to
both append to ``state["trace_events"]`` and push ``review.format.*`` via
``get_stream_writer`` for unified ``<domain>.<stage>`` namespace.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from standard_document_assistant.constants import OUTPUT_DIR, UPLOADS_DIR
from standard_document_assistant.graphs.standard_review.events import emit_event
from standard_document_assistant.graphs.standard_review.state import StandardReviewState
from standard_document_assistant.pathing import resolve_workspace_read_path
from standard_document_assistant.review_core.format_audit import run_format_source_audit
from standard_document_assistant.review_core.pdf_format_parser import parse_pdf_format_file
from standard_document_assistant.review_core.rule_models import AuditIssue
from standard_document_assistant.review_core.word_parser import parse_word_file
from standard_document_assistant.schemas import ReviewIssue


def format_review(
    state: StandardReviewState,
    runtime: Any = None,
) -> dict[str, Any]:
    source = state.get("source_path", "")
    if not source:
        return {
            "warnings": ["未提供原始 PDF/DOCX，格式轨审核已跳过。"],
            "trace_events": [emit_event(state, "format_review", "skipped")],
        }
    suffix = Path(source).suffix.lower()
    if suffix not in {".pdf", ".docx"}:
        return {
            "warnings": ["源文件不是 PDF/DOCX，格式轨审核已跳过。"],
            "trace_events": [emit_event(state, "format_review", "skipped")],
        }
    try:
        source_host, source_virtual = resolve_workspace_read_path(
            source,
            allowed_roots=[UPLOADS_DIR, OUTPUT_DIR],
            suffixes={".pdf", ".docx"},
        )
    except Exception as exc:
        return {
            "errors": [f"源文件不可读取：{exc}"],
            "warnings": [f"源文件不可读取，格式轨将跳过：{exc}"],
            "trace_events": [emit_event(state, "format_review", "failed")],
        }
    try:
        if suffix == ".docx":
            document = parse_word_file(str(source_host))
        else:
            document = parse_pdf_format_file(str(source_host))
        source_issues, format_trace = run_format_source_audit(document)
        issues = [
            _source_audit_issue_to_review_issue(item, source_virtual).model_dump()
            for item in source_issues
        ]
        if not issues and int(format_trace.get("facts_total") or 0) == 0:
            issues = [
                ReviewIssue(
                    issue_id="FMT-SOURCE-INFO",
                    rule_id="FMT-SOURCE-000",
                    rule_name="格式轨审核依据不足",
                    scope="full_document",
                    audit_track="format_source",
                    severity="info",
                    status="insufficient_context",
                    expected="格式轨应基于原始 DOCX/PDF 的结构化格式事实执行确定性检查。",
                    actual="未从源文件抽取到格式事实。",
                    evidence_text=source_virtual,
                    source_ref="format_source::availability",
                    suggestion="确认 DOCX/PDF 具有可识别标题、段落和可复制文字层。",
                    confidence=0.0,
                    llm_reasoning="源解析没有产生格式事实，未调用 LLM。",
                ).model_dump()
            ]
    except Exception as exc:
        issue = ReviewIssue(
            issue_id="FMT-SOURCE-INFO",
            rule_id="FMT-SOURCE-000",
            rule_name="格式轨审核依据不足",
            scope="full_document",
            audit_track="format_source",
            severity="info",
            status="insufficient_context",
            expected="格式轨应基于原始 DOCX/PDF 的结构化格式事实执行确定性检查。",
            actual=str(exc),
            evidence_text=source_virtual,
            source_ref="format_source::availability",
            suggestion="确认源文件可读取；PDF 需具备可复制文字层并安装 pymupdf，DOCX 需安装 python-docx/lxml。",
            confidence=0.0,
            llm_reasoning="未获得足够格式事实，未调用 LLM。",
        )
        issues = [issue.model_dump()]
        format_trace = {"enabled": False, "source_type": suffix.lstrip("."), "error": str(exc)}
        warnings = [str(exc)]
    else:
        warnings = []
    return {
        "issues": issues,
        "warnings": warnings,
        "format_facts": format_trace,
        "trace_events": [
            emit_event(
                state,
                "format_review",
                "success",
                {
                    "source_type": suffix.lstrip("."),
                    "issues": len(issues),
                    "facts_total": format_trace.get("facts_total", 0),
                },
            )
        ],
    }


def _source_audit_issue_to_review_issue(issue: AuditIssue, source_virtual: str) -> ReviewIssue:
    severity_map = {
        "严重": "critical",
        "重度": "critical",
        "中度": "major",
        "轻度": "minor",
        "提示": "info",
        "warn": "minor",
    }
    status = issue.status
    if status == "not_ready":
        status = "insufficient_context"
    if status not in {"pass", "fail", "warn", "insufficient_context", "llm_error"}:
        status = "warn"
    evidence = issue.evidence_text or ""
    if source_virtual and not evidence.startswith(source_virtual):
        evidence = f"{source_virtual} | {evidence}".strip()
    return ReviewIssue(
        issue_id=issue.issue_id,
        rule_id=issue.rule_id,
        rule_name=issue.rule_name,
        scope=issue.scope,
        audit_track="format_source",
        severity=severity_map.get(issue.severity, "major"),  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        expected=issue.expected,
        actual=issue.actual,
        evidence_text=evidence[:1000],
        source_ref=issue.source_ref,
        suggestion=issue.suggestion,
        confidence=issue.confidence,
        llm_reasoning=issue.llm_reasoning,
    )


# 旧 _event 辅助函数已迁移至 ``standard_document_assistant.graphs.standard_review.events.emit_event``。
# 旧 _event 仅写 state["trace_events"]；新 emit_event 既写 state["trace_events"]，也通过
# get_stream_writer 推送 ``review.format.*`` 事件，与 MinerU ``mineru.*``、langextract
# ``meta.*`` 形成统一 ``<domain>.<stage>`` 命名空间（2026-06-03 rev. 3）。
