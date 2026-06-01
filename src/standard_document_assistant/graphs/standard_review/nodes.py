"""Nodes for the standard review P0 graph."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from standard_document_assistant.config import load_config
from standard_document_assistant.constants import OUTPUT_DIR, REVIEWS_OUTPUT_DIR, UPLOADS_DIR
from standard_document_assistant.graphs.standard_review.state import StandardReviewState
from standard_document_assistant.pathing import (
    allocate_unique_path,
    host_to_virtual_path,
    resolve_workspace_read_path,
    review_output_root,
    safe_name,
    utc_now_iso,
    write_json,
)
from standard_document_assistant.review_core.reporter import render_markdown_report
from standard_document_assistant.review_core.format_audit import run_format_source_audit
from standard_document_assistant.review_core.pdf_format_parser import parse_pdf_format_file
from standard_document_assistant.review_core.rules import load_review_rules
from standard_document_assistant.review_core.rule_models import AuditIssue
from standard_document_assistant.review_core.word_parser import parse_word_file
from standard_document_assistant.schemas import ArtifactManifest, ArtifactRef, ReviewIssue
from standard_document_assistant.tracing import STANDARD_REVIEW_TOOL_NAME


def ingest(state: StandardReviewState) -> dict[str, Any]:
    content_path = state.get("content_path", "")
    source_path = state.get("source_path", "")
    manifest_path = state.get("manifest_path", "")
    warnings: list[str] = []
    trace = [_event(state, "ingest", "started")]
    manifest_data: dict[str, Any] = {}

    if manifest_path:
        manifest_host, manifest_virtual = resolve_workspace_read_path(
            manifest_path,
            allowed_roots=[UPLOADS_DIR, OUTPUT_DIR],
            suffixes={".json"},
        )
        manifest_path = manifest_virtual
        manifest_data = json.loads(manifest_host.read_text(encoding="utf-8"))
        content_path = content_path or _manifest_markdown_path(manifest_data)
        source_path = source_path or manifest_data.get("source_virtual_path", "")

    if not content_path and state.get("format_only") and source_path:
        resolved_source_virtual = ""
        _, resolved_source_virtual = resolve_workspace_read_path(
            source_path,
            allowed_roots=[UPLOADS_DIR, OUTPUT_DIR],
            suffixes={".pdf", ".docx"},
        )
        return {
            "content_path": "",
            "source_path": resolved_source_virtual,
            "manifest_path": manifest_path,
            "parsed_document": {
                "content_path": "",
                "source_path": resolved_source_virtual,
                "manifest_path": manifest_path,
                "char_count": 0,
                "scope_count": 0,
                "manifest": manifest_data,
            },
            "scope_text_map": {},
            "active_scope_keys": [],
            "warnings": warnings,
            "trace_events": trace + [_event(state, "ingest", "success")],
        }

    if not content_path:
        if source_path and Path(source_path).suffix.lower() in {".pdf", ".docx"}:
            return {
                "status": "failed",
                "errors": ["缺少 MinerU Markdown；请先调用 parse_document_with_mineru。"],
                "warnings": warnings,
                "trace_events": trace + [_event(state, "ingest", "failed")],
            }
        return {
            "status": "failed",
            "errors": ["缺少 content_path 或可解析的 manifest_path。"],
            "warnings": warnings,
            "trace_events": trace + [_event(state, "ingest", "failed")],
        }

    content_host, content_virtual = resolve_workspace_read_path(
        content_path,
        allowed_roots=[UPLOADS_DIR, OUTPUT_DIR],
        suffixes={".md", ".markdown", ".txt"},
    )
    markdown = content_host.read_text(encoding="utf-8", errors="ignore")
    markdown = _slice_lines(markdown, state.get("line_start"), state.get("line_end"))
    scope_text_map = _split_markdown_scopes(markdown)
    target_scopes = state.get("target_scopes")
    if target_scopes:
        scope_text_map = {
            key: value for key, value in scope_text_map.items() if key in set(target_scopes)
        }
    if not scope_text_map:
        scope_text_map = {"full_document": markdown}
        warnings.append("未匹配到目标 scope，已退回全文审核。")

    resolved_source_virtual = ""
    if source_path:
        try:
            _, resolved_source_virtual = resolve_workspace_read_path(
                source_path,
                allowed_roots=[UPLOADS_DIR, OUTPUT_DIR],
                suffixes={".pdf", ".docx", ".md", ".markdown", ".txt"},
            )
        except Exception as exc:
            warnings.append(f"源文件不可读取，格式轨将跳过：{exc}")

    return {
        "content_path": content_virtual,
        "source_path": resolved_source_virtual,
        "manifest_path": manifest_path,
        "parsed_document": {
            "content_path": content_virtual,
            "source_path": resolved_source_virtual,
            "manifest_path": manifest_path,
            "char_count": len(markdown),
            "scope_count": len(scope_text_map),
            "manifest": manifest_data,
        },
        "scope_text_map": scope_text_map,
        "active_scope_keys": list(scope_text_map.keys()),
        "warnings": warnings,
        "trace_events": trace + [_event(state, "ingest", "success")],
    }


def retrieve_rules(state: StandardReviewState) -> dict[str, Any]:
    rules, metadata = load_review_rules()
    top_k = int(state.get("top_k") or load_config().standard_review.top_k)
    section_rules: list[dict[str, Any]] = []
    full_rules: list[dict[str, Any]] = []
    retrieval_trace: list[dict[str, Any]] = []
    scopes = state.get("active_scope_keys") or ["full_document"]
    requested_scopes = set(state.get("target_scopes") or [])
    for required_scope in ["scope", "normative_references"]:
        if requested_scopes and required_scope not in requested_scopes:
            continue
        if required_scope not in scopes:
            scopes.append(required_scope)
    for scope in scopes:
        candidates = _rank_rules(scope, rules)[:top_k]
        section_rules.extend({**rule, "matched_scope": scope} for rule in candidates)
        retrieval_trace.append(
            {
                "scope": scope,
                "top_k": top_k,
                "rule_ids": [rule.get("rule_id", "") for rule in candidates],
            }
        )
    full_rules = [rule for rule in rules if rule.get("scope") == "full_document"]
    return {
        "section_rules": section_rules,
        "full_document_rules": full_rules,
        "retrieval_trace": retrieval_trace,
        "rules_metadata": metadata,
        "trace_events": [_event(state, "retrieve_rules", "success")],
    }


def content_review(state: StandardReviewState) -> dict[str, Any]:
    if state.get("format_only"):
        return {"trace_events": [_event(state, "content_review", "skipped")]}
    scope_text_map = state.get("scope_text_map") or {}
    issues: list[dict[str, Any]] = []
    issue_no = 1
    for rule in state.get("section_rules") or []:
        scope = rule.get("matched_scope") or rule.get("scope") or "full_document"
        text = scope_text_map.get(scope, "")
        issue = _evaluate_content_rule(rule, scope, text, issue_no)
        if issue:
            issues.append(issue)
            issue_no += 1
    return {
        "issues": issues,
        "trace_events": [_event(state, "content_review", "success", {"issues": len(issues)})],
    }


def format_review(state: StandardReviewState) -> dict[str, Any]:
    source = state.get("source_path", "")
    if not source:
        return {
            "warnings": ["未提供原始 PDF/DOCX，格式轨审核已跳过。"],
            "trace_events": [_event(state, "format_review", "skipped")],
        }
    suffix = Path(source).suffix.lower()
    if suffix not in {".pdf", ".docx"}:
        return {
            "warnings": ["源文件不是 PDF/DOCX，格式轨审核已跳过。"],
            "trace_events": [_event(state, "format_review", "skipped")],
        }
    source_host, source_virtual = resolve_workspace_read_path(
        source,
        allowed_roots=[UPLOADS_DIR, OUTPUT_DIR],
        suffixes={".pdf", ".docx"},
    )
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
        warnings: list[str] = []
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
        warnings = [str(exc)]
        format_trace = {"enabled": False, "source_type": suffix.lstrip("."), "error": str(exc)}
    return {
        "issues": issues,
        "warnings": warnings,
        "format_facts": format_trace,
        "trace_events": [
            _event(
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


def aggregate(state: StandardReviewState) -> dict[str, Any]:
    issues = state.get("issues") or []
    statuses = Counter(item.get("status", "") for item in issues)
    severities = Counter(item.get("severity", "") for item in issues)
    tracks = Counter(item.get("audit_track", "") for item in issues)
    summary = {
        "total_issues": len(issues),
        "failed": statuses.get("fail", 0),
        "warn": statuses.get("warn", 0),
        "insufficient_context": statuses.get("insufficient_context", 0),
        "by_severity": dict(severities),
        "by_track": dict(tracks),
    }
    return {
        "aggregate_summary": summary,
        "status": "failed" if state.get("errors") else "success",
        "trace_events": [_event(state, "aggregate", "success")],
    }


def write_report(state: StandardReviewState) -> dict[str, Any]:
    output_dir = review_output_root(state.get("output_subdir") or state.get("job_id") or "")
    stem = safe_name(Path(state.get("content_path") or "standard").stem, fallback="standard")
    report_path = allocate_unique_path(output_dir, f"{stem}_audit_report", ".md")
    result_path = allocate_unique_path(output_dir, f"{stem}_audit_result", ".json")
    trace_path = allocate_unique_path(output_dir, f"{stem}_audit_trace", ".json")

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
        "issues": state.get("issues") or [],
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
        "output_paths": paths,
        "trace_events": [_event(state, "write_report", "success")],
    }


def write_manifest(state: StandardReviewState) -> dict[str, Any]:
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
    write_json(manifest_path, payload)
    output_paths["manifest"] = host_to_virtual_path(manifest_path)
    return {
        "output_paths": output_paths,
        "trace_events": [_event(state, "write_manifest", "success")],
    }


def _manifest_markdown_path(manifest: dict[str, Any]) -> str:
    primary = manifest.get("primary_artifact") or {}
    if primary.get("type") == "markdown":
        return primary.get("virtual_path", "")
    for item in manifest.get("artifacts") or []:
        if item.get("type") == "markdown":
            return item.get("virtual_path", "")
    return ""


def _split_markdown_scopes(markdown: str) -> dict[str, str]:
    headings = list(re.finditer(r"(?m)^(#{1,6})\s+(.+?)\s*$", markdown))
    if not headings:
        return {"full_document": markdown}
    scopes: dict[str, str] = {}
    for idx, match in enumerate(headings):
        title = match.group(2).strip()
        start = match.start()
        end = headings[idx + 1].start() if idx + 1 < len(headings) else len(markdown)
        scope = _scope_from_heading(title)
        scopes.setdefault(scope, "")
        scopes[scope] += markdown[start:end].strip() + "\n"
    scopes.setdefault("full_document", markdown)
    return scopes


def _scope_from_heading(title: str) -> str:
    if "范围" in title:
        return "scope"
    if "规范性引用" in title or "引用文件" in title:
        return "normative_references"
    if "术语" in title or "定义" in title:
        return "terms"
    return safe_name(title, fallback="section").lower()


def _slice_lines(text: str, start: int | None, end: int | None) -> str:
    if start is None and end is None:
        return text
    lines = text.splitlines()
    start_idx = max((start or 1) - 1, 0)
    end_idx = end if end is not None else len(lines)
    return "\n".join(lines[start_idx:end_idx])


def _rank_rules(scope: str, rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    preferred = [rule for rule in rules if rule.get("scope") in {scope, "full_document"}]
    return preferred or rules


def _evaluate_content_rule(
    rule: dict[str, Any],
    scope: str,
    text: str,
    issue_no: int,
) -> dict[str, Any] | None:
    if scope == "full_document":
        return None
    if text.strip():
        return None
    status = "insufficient_context" if scope not in {"scope", "normative_references"} else "warn"
    issue = ReviewIssue(
        issue_id=f"ISSUE-{issue_no:03d}",
        rule_id=str(rule.get("rule_id", "")),
        rule_name=str(rule.get("rule_name", "")),
        scope=scope,
        audit_track="content",
        severity=str(rule.get("severity") or "major"),  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        expected=str(rule.get("text") or rule.get("rule_name") or ""),
        actual="未在 Markdown 中定位到对应章节内容。",
        evidence_text="",
        source_ref=str(rule.get("source_ref", "")),
        suggestion="补充对应章节，或在报告中说明该章节不适用的原因。",
        confidence=0.4,
        llm_reasoning="P0 框架使用确定性章节存在性检查，尚未启用 LLM 内容判定。",
    )
    return issue.model_dump()


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


def _event(
    state: StandardReviewState,
    node: str,
    status: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "trace_id": state.get("trace_id", ""),
        "job_id": state.get("job_id", ""),
        "component": "standard_review_graph",
        "node": node,
        "event": node,
        "status": status,
        "created_at": utc_now_iso(),
    }
    if extra:
        payload.update(extra)
    return payload
