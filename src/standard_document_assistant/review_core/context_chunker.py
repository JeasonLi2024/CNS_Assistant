"""Per-rule context construction for the LLM judge.

Given a parsed document and a ``RuleItem``, build a string that contains
exactly the bits the LLM needs to compare against the rule. Context size is
bounded by the configured character limits; longer inputs are windowed
with overlap so the model still sees the relevant area.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from standard_document_assistant.config import StandardReviewConfig
from standard_document_assistant.review_core.doc_parser import (
    DocumentSectionChunk,
    ParsedMarkdownDocument,
)
from standard_document_assistant.review_core.rule_models import RuleItem
from standard_document_assistant.review_core.scopes import build_scope_text_map


@dataclass
class _DocCache:
    document_id: int
    ordered_units: list[dict[str, Any]] = field(default_factory=list)
    structural_overview: dict[str, Any] = field(default_factory=dict)


class DocumentContextBuilder:
    """Build per-rule review context from a parsed document."""

    def __init__(self, config: StandardReviewConfig) -> None:
        self.config = config
        self._cache: dict[int, _DocCache] = {}

    def reset_for_document(self, document: ParsedMarkdownDocument) -> None:
        self._cache.pop(id(document), None)

    def build_rule_context(
        self,
        rule: RuleItem,
        document: ParsedMarkdownDocument,
        scope_text_map: dict[str, str] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        scope_map = scope_text_map or build_scope_text_map(document)
        target_scopes = list(rule.target_scopes or [rule.scope])
        if rule.analysis_mode == "full_document":
            target_scopes = ["cover", "toc", "foreword", "body", "end"]

        units = self._ordered_units(document, scope_map)
        selected = self._select_relevant_chunks(units, target_scopes)
        selection_meta = {
            "target_scopes": target_scopes,
            "chunks_total": len(units),
            "chunks_used": len(selected),
        }
        return selected, selection_meta

    def structural_overview(self, document: ParsedMarkdownDocument) -> dict[str, Any]:
        cache = self._ensure_cache(document)
        if cache.structural_overview:
            return cache.structural_overview
        scope_map = build_scope_text_map(document)
        overview = {
            "file_name": document.file_name,
            "scope_lengths": {key: len(value or "") for key, value in scope_map.items()},
        }
        cache.structural_overview = overview
        return overview

    def _ensure_cache(self, document: ParsedMarkdownDocument) -> _DocCache:
        doc_id = id(document)
        if doc_id not in self._cache:
            self._cache[doc_id] = _DocCache(document_id=doc_id)
        return self._cache[doc_id]

    def _ordered_units(
        self, document: ParsedMarkdownDocument, scope_map: dict[str, str]
    ) -> list[dict[str, Any]]:
        cache = self._ensure_cache(document)
        if cache.ordered_units:
            return cache.ordered_units
        units: list[dict[str, Any]] = []
        order = 1
        if (document.cover_text or "").strip():
            units.append(
                {
                    "order": order,
                    "scope": "cover",
                    "heading": "cover",
                    "line_start": document.cover_line_start,
                    "line_end": document.cover_line_end,
                    "text": document.cover_text.strip(),
                }
            )
            order += 1
        if (document.toc_text or "").strip():
            units.append(
                {
                    "order": order,
                    "scope": "toc",
                    "heading": "toc",
                    "line_start": document.toc_line_start,
                    "line_end": document.toc_line_end,
                    "text": document.toc_text.strip(),
                }
            )
            order += 1
        for chunk in document.section_chunks:
            units.append(
                {
                    "order": order,
                    "scope": chunk.scope,
                    "heading": chunk.heading,
                    "line_start": chunk.line_start,
                    "line_end": chunk.line_end,
                    "text": chunk.text,
                }
            )
            order += 1
        cache.ordered_units = units
        return units

    def _select_relevant_chunks(
        self, units: list[dict[str, Any]], target_scopes: list[str]
    ) -> str:
        body_scopes = {
            "scope",
            "normative_references",
            "terms_definitions",
            "symbols_abbreviations",
            "other_body",
        }
        front_matter_scopes = {"foreword", "introduction"}
        end_scopes = {"appendix", "index", "references", "end"}

        limit = self.config.local_context_max_chars
        selected: list[str] = []
        total = 0
        for unit in units:
            if unit["scope"] in target_scopes or unit["scope"] in body_scopes:
                if unit["scope"] in body_scopes and "other_body" not in target_scopes and "body" not in target_scopes:
                    if not any(scope in body_scopes for scope in target_scopes):
                        continue
                text = (unit.get("text") or "").strip()
                if not text:
                    continue
                if total + len(text) > limit and selected:
                    break
                selected.append(f"## {unit['heading'] or unit['scope']}\n{text}")
                total += len(text)
        if not selected:
            for unit in units:
                if unit["scope"] in front_matter_scopes:
                    text = (unit.get("text") or "").strip()
                    if text:
                        selected.append(f"## {unit['heading'] or unit['scope']}\n{text}")
                        total += len(text)
                        if total > limit:
                            break
        if not selected:
            for unit in units:
                if unit["scope"] in end_scopes:
                    text = (unit.get("text") or "").strip()
                    if text:
                        selected.append(f"## {unit['heading'] or unit['scope']}\n{text}")
                        total += len(text)
                        if total > limit:
                            break
        if not selected:
            text = (units[0]["text"] if units else "").strip()
            if text:
                selected.append(text[:limit])
        return "\n\n".join(selected).strip()
