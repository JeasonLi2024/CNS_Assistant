"""Scope normalization and document restriction helpers.

The standard review workflow operates on a per-scope view of the parsed
document. These helpers keep the "active scopes" semantics consistent
between the ingest, retrieve, review, and aggregate subgraphs.
"""

from __future__ import annotations

from typing import Any, Iterable

from standard_document_assistant.review_core.doc_parser import (
    ParsedMarkdownDocument,
    build_scope_text_map as _doc_parser_build_scope_text_map,
)
from standard_document_assistant.review_core.serialization import (
    deserialize_document,
    serialize_document,
)


DEFAULT_REVIEW_SCOPE_ORDER: tuple[str, ...] = (
    "cover",
    "toc",
    "foreword",
    "introduction",
    "scope",
    "normative_references",
    "terms_definitions",
    "symbols_abbreviations",
    "other_body",
    "appendix",
    "references",
    "index",
    "end",
)


SCOPE_ALIASES: dict[str, str] = {
    "范围": "scope",
    "scope": "scope",
    "引用文件": "normative_references",
    "规范性引用": "normative_references",
    "normative_references": "normative_references",
    "术语": "terms_definitions",
    "术语和定义": "terms_definitions",
    "terms_definitions": "terms_definitions",
    "符号": "symbols_abbreviations",
    "符号和缩略语": "symbols_abbreviations",
    "symbols_abbreviations": "symbols_abbreviations",
    "前言": "foreword",
    "foreword": "foreword",
    "引言": "introduction",
    "introduction": "introduction",
    "目次": "toc",
    "目录": "toc",
    "toc": "toc",
    "封面": "cover",
    "cover": "cover",
    "正文": "other_body",
    "body": "other_body",
    "other_body": "other_body",
    "附录": "appendix",
    "appendix": "appendix",
    "参考文献": "references",
    "references": "references",
    "索引": "index",
    "index": "index",
    "结束": "end",
    "end": "end",
}


def normalize_scope_keys(values: Iterable[str] | None) -> list[str]:
    if not values:
        return []
    normalized: list[str] = []
    for value in values:
        if not value:
            continue
        key = SCOPE_ALIASES.get(str(value).strip(), str(value).strip())
        if key not in normalized:
            normalized.append(key)
    return normalized


def build_scope_text_map(doc: ParsedMarkdownDocument) -> dict[str, str]:
    return _doc_parser_build_scope_text_map(doc)


def restrict_document(
    document: ParsedMarkdownDocument,
    active_scope_keys: Iterable[str],
    partial_mode: str,
) -> ParsedMarkdownDocument:
    """Build a "review view" of the document restricted to the active scopes."""

    keys = list(dict.fromkeys(active_scope_keys))
    if partial_mode == "full_document" or not keys:
        view = "\n\n".join(
            (getattr(document, f"{key}_text", "") or "").strip()
            for key in DEFAULT_REVIEW_SCOPE_ORDER
            if (getattr(document, f"{key}_text", "") or "").strip()
        ).strip()
    else:
        view = "\n\n".join(
            f"# {key}\n{(getattr(document, f'{key}_text', '') or '').strip()}"
            for key in keys
            if (getattr(document, f"{key}_text", "") or "").strip()
        ).strip()
    if not view:
        view = (document.text_view or document.raw_text or "").strip()
    new_doc = ParsedMarkdownDocument(
        file_name=document.file_name,
        raw_text=view,
        cover_text=document.cover_text if "cover" in keys or partial_mode == "full_document" else "",
        toc_text=document.toc_text if "toc" in keys or partial_mode == "full_document" else "",
        body_text=view,
        lines=view.splitlines() if view else [],
    )
    new_doc.text_view = view
    new_doc.source_type = document.source_type
    new_doc.format_facts = list(document.format_facts)
    new_doc.source_locations = dict(document.source_locations or {})
    new_doc.cover_line_start = document.cover_line_start
    new_doc.cover_line_end = document.cover_line_end
    new_doc.toc_line_start = document.toc_line_start
    new_doc.toc_line_end = document.toc_line_end
    for key in DEFAULT_REVIEW_SCOPE_ORDER:
        if partial_mode != "full_document" and key not in keys:
            setattr(new_doc, f"{key}_text", "")
    return new_doc


def restrict_document_payload(
    payload: dict[str, Any],
    active_scope_keys: list[str],
    partial_mode: str,
) -> dict[str, Any]:
    doc = deserialize_document(payload)
    restricted = restrict_document(doc, active_scope_keys, partial_mode)
    return serialize_document(restricted)


def filter_rules_for_partial_mode(
    rules,
    partial_mode: str,
    active_keys: list[str],
    *,
    include_full_document_scope: bool = True,
):
    if partial_mode == "full_document":
        return list(rules)
    keys = set(active_keys)
    filtered = []
    for rule in rules:
        if rule.analysis_mode == "full_document" and not include_full_document_scope:
            continue
        if rule.analysis_mode in {"local", "cross_section"}:
            if rule.target_scopes and keys.intersection(rule.target_scopes):
                filtered.append(rule)
        elif rule.analysis_mode == "full_document":
            if include_full_document_scope:
                filtered.append(rule)
        else:
            filtered.append(rule)
    return filtered


def expand_cross_section_scope_keys(
    active_keys: list[str],
    rules,
    partial_mode: str,
) -> tuple[list[str], list[str]]:
    if partial_mode == "full_document" or not rules:
        return active_keys, []
    keys = set(active_keys)
    warnings: list[str] = []
    for rule in rules:
        if rule.analysis_mode == "cross_section":
            for target in rule.target_scopes or [rule.scope]:
                if target not in keys:
                    keys.add(target)
                    warnings.append(
                        f"规则 {rule.chunk_id} 需要章节 {target}，已自动加入审核范围。"
                    )
    return [key for key in DEFAULT_REVIEW_SCOPE_ORDER if key in keys], warnings
