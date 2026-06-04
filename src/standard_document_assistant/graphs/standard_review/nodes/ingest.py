"""Ingest subgraph for standard review.

Reads the manifest, the MinerU Markdown content, and (optionally) the
source DOCX/PDF for the format track. Stores everything on state under
``parsed_document`` and ``scope_text_map``.

Stream events (2026-06-03 rev. 3): uses shared ``emit_event`` helper to
both append to ``state["trace_events"]`` and push ``review.ingest.*`` via
``get_stream_writer`` for unified ``<domain>.<stage>`` namespace.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from standard_document_assistant.config import load_config
from standard_document_assistant.constants import OUTPUT_DIR, UPLOADS_DIR
from standard_document_assistant.graphs.standard_review.events import emit_event
from standard_document_assistant.graphs.standard_review.state import StandardReviewState
from standard_document_assistant.pathing import (
    resolve_workspace_read_path,
)
from standard_document_assistant.review_core.doc_parser import parse_markdown_document
from standard_document_assistant.review_core.scopes import build_scope_text_map, normalize_scope_keys
from standard_document_assistant.review_core.serialization import serialize_document


def _max_review_rounds_from_state(state: StandardReviewState) -> int:
    value = state.get("max_review_rounds")
    if value is None:
        return int(load_config().standard_review.max_review_rounds)
    return int(value)


def ingest(state: StandardReviewState, runtime: Any = None) -> dict[str, Any]:
    warnings: list[str] = []
    errors: list[str] = []
    trace = [emit_event(state, "ingest", "started")]

    content_path = state.get("content_path", "")
    source_path = state.get("source_path", "")
    manifest_path = state.get("manifest_path", "")
    manifest_data: dict[str, Any] = {}

    if manifest_path:
        manifest_host, manifest_virtual = resolve_workspace_read_path(
            manifest_path,
            allowed_roots=[UPLOADS_DIR, OUTPUT_DIR],
            suffixes={".json"},
        )
        manifest_path = manifest_virtual
        try:
            manifest_data = json.loads(manifest_host.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"manifest 解析失败：{exc}")
            manifest_data = {}
        content_path = content_path or _manifest_markdown_path(manifest_data)
        source_path = source_path or manifest_data.get("source_virtual_path", "")

    if not content_path and state.get("format_only") and source_path:
        try:
            _, resolved_source_virtual = resolve_workspace_read_path(
                source_path,
                allowed_roots=[UPLOADS_DIR, OUTPUT_DIR],
                suffixes={".pdf", ".docx"},
            )
        except Exception as exc:
            errors.append(f"源文件不可读取：{exc}")
            resolved_source_virtual = source_path
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
            "partial_mode": "format_only",
            "review_round": 0,
            "max_review_rounds": _max_review_rounds_from_state(state),
            "errors": errors,
            "warnings": warnings,
            "trace_events": trace + [emit_event(state, "ingest", "format_only")],
        }

    if not content_path:
        errors.append(
            "缺少 content_path 或可解析的 manifest_path。"
            "若源文件是 PDF/DOCX，请先调用 parse_file_with_mineru 解析后再传入 Markdown 路径。"
        )
        return {
            "status": "failed",
            "errors": errors,
            "warnings": warnings,
            "trace_events": trace + [emit_event(state, "ingest", "failed")],
        }

    try:
        content_host, content_virtual = resolve_workspace_read_path(
            content_path,
            allowed_roots=[UPLOADS_DIR, OUTPUT_DIR],
            suffixes={".md", ".markdown", ".txt"},
        )
    except Exception as exc:
        errors.append(f"Markdown 路径不可访问：{exc}")
        return {
            "status": "failed",
            "errors": errors,
            "warnings": warnings,
            "trace_events": trace + [emit_event(state, "ingest", "failed")],
        }

    markdown = content_host.read_text(encoding="utf-8", errors="ignore")
    markdown = _slice_lines(markdown, state.get("line_start"), state.get("line_end"))

    document = parse_markdown_document(markdown, file_name=content_host.name, raw_view=markdown)
    document.text_view = markdown
    document.cover_text, document.toc_text, document.body_text, _rest = _split_markdown_fallback(markdown)
    serialized = serialize_document(document)
    serialized["file_name"] = content_host.name
    serialized["content_path"] = content_virtual
    serialized["manifest_path"] = manifest_path
    serialized["manifest"] = manifest_data

    target_scopes = normalize_scope_keys(state.get("target_scopes"))
    scope_text_map = build_scope_text_map(document)
    if target_scopes:
        scope_text_map = {key: value for key, value in scope_text_map.items() if key in set(target_scopes)}
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

    partial_mode = state.get("partial_mode") or "sectional"
    if partial_mode not in {"sectional", "full_document", "format_only"}:
        partial_mode = "sectional"

    serialized["source_path"] = resolved_source_virtual
    serialized["char_count"] = len(markdown)
    serialized["scope_count"] = len(scope_text_map)
    return {
        "content_path": content_virtual,
        "source_path": resolved_source_virtual,
        "manifest_path": manifest_path,
        "parsed_document": serialized,
        "scope_text_map": scope_text_map,
        "active_scope_keys": list(scope_text_map.keys()),
        "partial_mode": partial_mode,
        "review_round": int(state.get("review_round") or 0),
        "max_review_rounds": _max_review_rounds_from_state(state),
        "errors": errors,
        "warnings": warnings,
        "trace_events": trace + [emit_event(state, "ingest", "success", {"scope_count": len(scope_text_map)})],
    }


def _manifest_markdown_path(manifest: dict[str, Any]) -> str:
    primary = manifest.get("primary_artifact") or {}
    if primary.get("type") == "markdown":
        return primary.get("virtual_path", "")
    for item in manifest.get("artifacts") or []:
        if item.get("type") == "markdown":
            return item.get("virtual_path", "")
    return ""


def _slice_lines(text: str, start: int | None, end: int | None) -> str:
    if start is None and end is None:
        return text
    lines = text.splitlines()
    start_idx = max((start or 1) - 1, 0)
    end_idx = end if end is not None else len(lines)
    return "\n".join(lines[start_idx:end_idx])


def _split_markdown_fallback(markdown: str) -> tuple[str, str, str, str]:
    """Best-effort splitting when ``doc_parser`` produced no sections."""

    headings = list(re.finditer(r"(?m)^(#{1,6})\s+(.+?)\s*$", markdown))
    if not headings:
        return "", "", markdown, ""
    sections: dict[str, str] = {}
    for idx, match in enumerate(headings):
        title = match.group(2).strip()
        start = match.start()
        end = headings[idx + 1].start() if idx + 1 < len(headings) else len(markdown)
        body = markdown[start:end].strip()
        if "范围" in title:
            sections.setdefault("scope", body)
        elif "规范性引用" in title or "引用文件" in title:
            sections.setdefault("normative_references", body)
        elif "术语" in title or "定义" in title:
            sections.setdefault("terms_definitions", body)
        elif "目次" in title or "目录" in title:
            sections.setdefault("toc", body)
        elif "前言" in title:
            sections.setdefault("foreword", body)
        elif "封面" in title:
            sections.setdefault("cover", body)
        else:
            sections.setdefault("other_body", body)
    return (
        sections.get("cover", ""),
        sections.get("toc", ""),
        sections.get("other_body", markdown),
        sections.get("scope", ""),
    )


# 旧 _event 辅助函数已迁移至 ``standard_document_assistant.graphs.standard_review.events.emit_event``。
# 旧 _event 仅写 state["trace_events"]；新 emit_event 既写 state["trace_events"]，也通过
# get_stream_writer 推送 ``review.ingest.*`` 事件，与 MinerU ``mineru.*``、langextract
# ``meta.*`` 形成统一 ``<domain>.<stage>`` 命名空间（2026-06-03 rev. 3）。
