"""Serialization helpers for review core data models.

LangGraph state values must be JSON-serializable. ``ParsedMarkdownDocument``,
``RuleItem`` and ``AuditIssue`` are Python dataclasses; we serialize them
through these helpers before they enter graph state and deserialize on the
way back.
"""

from __future__ import annotations

from typing import Any

from standard_document_assistant.review_core.doc_parser import (
    DocumentSectionChunk,
    ParsedMarkdownDocument,
)
from standard_document_assistant.review_core.rule_models import AuditIssue, RuleItem


def serialize_section_chunks(chunks: list[DocumentSectionChunk]) -> list[dict[str, Any]]:
    return [
        {
            "order": chunk.order,
            "scope": chunk.scope,
            "heading": chunk.heading,
            "text": chunk.text,
            "line_start": chunk.line_start,
            "line_end": chunk.line_end,
        }
        for chunk in chunks
    ]


def deserialize_section_chunks(payload: list[dict[str, Any]] | None) -> list[DocumentSectionChunk]:
    if not payload:
        return []
    return [
        DocumentSectionChunk(
            order=int(item.get("order", idx + 1)),
            scope=str(item.get("scope") or "other_body"),
            heading=str(item.get("heading") or ""),
            text=str(item.get("text") or ""),
            line_start=int(item.get("line_start") or 0),
            line_end=int(item.get("line_end") or 0),
        )
        for idx, item in enumerate(payload)
    ]


def serialize_document(doc: ParsedMarkdownDocument) -> dict[str, Any]:
    return {
        "file_name": doc.file_name,
        "raw_text": doc.raw_text,
        "cover_text": doc.cover_text,
        "toc_text": doc.toc_text,
        "body_text": doc.body_text,
        "lines": list(doc.lines),
        "cover_line_start": doc.cover_line_start,
        "cover_line_end": doc.cover_line_end,
        "toc_line_start": doc.toc_line_start,
        "toc_line_end": doc.toc_line_end,
        "foreword_text": doc.foreword_text,
        "introduction_text": doc.introduction_text,
        "scope_text": doc.scope_text,
        "normative_references_text": doc.normative_references_text,
        "terms_definitions_text": doc.terms_definitions_text,
        "symbols_abbreviations_text": doc.symbols_abbreviations_text,
        "other_body_text": doc.other_body_text,
        "appendix_text": doc.appendix_text,
        "index_text": doc.index_text,
        "references_text": doc.references_text,
        "end_text": doc.end_text,
        "section_chunks": serialize_section_chunks(doc.section_chunks),
        "source_type": doc.source_type,
        "text_view": doc.text_view,
        "format_facts": list(doc.format_facts),
        "source_locations": {key: dict(value) for key, value in (doc.source_locations or {}).items()},
    }


def deserialize_document(payload: dict[str, Any] | None) -> ParsedMarkdownDocument:
    if not payload:
        return ParsedMarkdownDocument(
            file_name="",
            raw_text="",
            cover_text="",
            toc_text="",
            body_text="",
            lines=[],
        )
    return ParsedMarkdownDocument(
        file_name=str(payload.get("file_name") or ""),
        raw_text=str(payload.get("raw_text") or ""),
        cover_text=str(payload.get("cover_text") or ""),
        toc_text=str(payload.get("toc_text") or ""),
        body_text=str(payload.get("body_text") or ""),
        lines=list(payload.get("lines") or []),
        cover_line_start=payload.get("cover_line_start"),
        cover_line_end=payload.get("cover_line_end"),
        toc_line_start=payload.get("toc_line_start"),
        toc_line_end=payload.get("toc_line_end"),
        foreword_text=str(payload.get("foreword_text") or ""),
        introduction_text=str(payload.get("introduction_text") or ""),
        scope_text=str(payload.get("scope_text") or ""),
        normative_references_text=str(payload.get("normative_references_text") or ""),
        terms_definitions_text=str(payload.get("terms_definitions_text") or ""),
        symbols_abbreviations_text=str(payload.get("symbols_abbreviations_text") or ""),
        other_body_text=str(payload.get("other_body_text") or ""),
        appendix_text=str(payload.get("appendix_text") or ""),
        index_text=str(payload.get("index_text") or ""),
        references_text=str(payload.get("references_text") or ""),
        end_text=str(payload.get("end_text") or ""),
        section_chunks=deserialize_section_chunks(payload.get("section_chunks")),
        source_type=str(payload.get("source_type") or "markdown"),
        text_view=str(payload.get("text_view") or payload.get("raw_text") or ""),
        format_facts=list(payload.get("format_facts") or []),
        source_locations={key: dict(value) for key, value in (payload.get("source_locations") or {}).items()},
    )


def serialize_rule(rule: RuleItem) -> dict[str, Any]:
    return rule.to_dict()


def deserialize_rule(payload: dict[str, Any] | None) -> RuleItem:
    if not payload:
        return RuleItem(chunk_id="", title="", scope="", content="", source_ref="")
    return RuleItem.from_dict(payload)


def serialize_issue(issue: AuditIssue) -> dict[str, Any]:
    return issue.to_dict()


def deserialize_issue(payload: dict[str, Any] | None) -> AuditIssue:
    if not payload:
        return AuditIssue(
            issue_id="",
            file_name="",
            rule_id="",
            rule_name="",
            scope="",
            severity="轻度",
            status="info",
            expected="",
            actual="",
            evidence_text="",
            source_ref="",
            suggestion="",
        )
    return AuditIssue(
        issue_id=str(payload.get("issue_id") or ""),
        file_name=str(payload.get("file_name") or ""),
        rule_id=str(payload.get("rule_id") or ""),
        rule_name=str(payload.get("rule_name") or ""),
        scope=str(payload.get("scope") or ""),
        severity=str(payload.get("severity") or "轻度"),
        status=str(payload.get("status") or "info"),
        expected=str(payload.get("expected") or ""),
        actual=str(payload.get("actual") or ""),
        evidence_text=str(payload.get("evidence_text") or ""),
        source_ref=str(payload.get("source_ref") or ""),
        suggestion=str(payload.get("suggestion") or ""),
        confidence=float(payload.get("confidence") or 0.0),
        llm_reasoning=str(payload.get("llm_reasoning") or ""),
    )
