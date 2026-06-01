from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .doc_parser import ParsedMarkdownDocument, parse_markdown_text

try:
    import fitz  # pymupdf
except ImportError:  # pragma: no cover
    fitz = None  # type: ignore[assignment,misc]


def parse_pdf_format_file(file_path: str) -> ParsedMarkdownDocument:
    """从 PDF 抽取格式审核所需结构事实（不做 PDF→Word 转换）。"""
    if fitz is None:
        raise RuntimeError("pymupdf_missing: 请安装 pymupdf 以审核 PDF 源文件的格式规范")

    path = Path(file_path)
    if path.suffix.lower() != ".pdf":
        raise ValueError("unsupported_format_source: 格式源文件须为 .pdf")

    doc = fitz.open(str(path))
    try:
        if doc.page_count == 0:
            raise ValueError("empty_pdf")
        toc_page_index, toc_text, toc_entries = _locate_and_parse_toc(doc)
        body_start = _find_body_start_page(doc, toc_page_index)
        facts, locations = _build_format_facts(doc, toc_page_index, body_start, toc_entries)
        text_view = _build_text_view(doc, toc_page_index, body_start)
    finally:
        doc.close()

    parsed = parse_markdown_text(text_view, file_name=path.name)
    parsed.source_type = "pdf"
    parsed.text_view = text_view
    parsed.toc_text = toc_text
    parsed.format_facts = facts
    parsed.source_locations = locations
    return parsed


def _locate_and_parse_toc(doc: Any) -> tuple[int, str, list[dict[str, Any]]]:
    for page_index in range(min(8, doc.page_count)):
        text = doc[page_index].get_text("text") or ""
        if re.search(r"目\s*次", text):
            entries = _parse_toc_page(text)
            return page_index, text.strip(), entries
    return -1, "", []


def _parse_toc_page(text: str) -> list[dict[str, Any]]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    entries: list[dict[str, Any]] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        if re.sub(r"\s+", "", ln) in {"目次", "目录"}:
            i += 1
            continue
        if re.fullmatch(r"[\.…．\s—\-GB/T\d]+", ln, re.I):
            i += 1
            continue
        if re.fullmatch(r"[IVXLCM]+", ln, re.I) and entries:
            prev = entries[-1]
            if prev.get("page") is None:
                prev["page"] = _roman_or_int(ln)
                prev["page_scheme"] = "roman_front_matter"
            i += 1
            continue
        if re.fullmatch(r"\d+", ln) and entries and entries[-1].get("page") is None:
            entries[-1]["page"] = int(ln)
            entries[-1]["page_scheme"] = "arabic_body"
            i += 1
            continue

        title = ln
        page: int | None = None
        scheme = "arabic_body"
        if re.sub(r"\s+", "", title) in {"前言", "引言"}:
            scheme = "roman_front_matter"
        if i + 1 < len(lines) and re.fullmatch(r"[IVXLCM]+", lines[i + 1], re.I):
            page = _roman_or_int(lines[i + 1])
            scheme = "roman_front_matter" if re.sub(r"\s+", "", title) in {"前言", "引言"} else scheme
            i += 2
        elif i + 1 < len(lines) and re.fullmatch(r"\d+", lines[i + 1]):
            page = int(lines[i + 1])
            i += 2
        else:
            m = re.search(r"([IVXLCM]+|\d+)\s*$", ln, re.I)
            if m:
                page = _roman_or_int(m.group(1))
                title = ln[: m.start()].strip()
                scheme = "roman_front_matter" if re.sub(r"\s+", "", title) in {"前言", "引言"} else "arabic_body"
            i += 1

        if not title or re.fullmatch(r"[\.…．]+", title):
            continue
        number, title_text = _split_number_title(title)
        entries.append(
            {
                "title": title,
                "title_text": title_text,
                "number": number,
                "page": page,
                "page_scheme": scheme,
                "display": title,
                "role": "toc_entry",
            }
        )
    return [e for e in entries if not _is_toc_self_title(e.get("title") or "")]


def _find_body_start_page(doc: Any, toc_page_index: int) -> int:
    start = max(toc_page_index + 1, 0)
    for page_index in range(start, doc.page_count):
        text = doc[page_index].get_text("text") or ""
        if re.search(r"前\s*言", text) and not re.search(r"目\s*次", text[:80]):
            return page_index
        if re.search(r"^1\s+范围", text, re.M):
            return page_index
    return min(start + 1, doc.page_count - 1)


def _footer_logical_page(page: Any) -> int | None:
    height = page.rect.height
    hits: list[tuple[float, str]] = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                y = span.get("origin", (0, 0))[1]
                text = (span.get("text") or "").strip()
                if text and y > height * 0.82 and re.fullmatch(r"[IVXLCM\d]+", text, re.I):
                    hits.append((y, text))
    if not hits:
        return None
    return _roman_or_int(sorted(hits, key=lambda x: x[0])[-1][1])


def _build_format_facts(
    doc: Any,
    toc_page_index: int,
    body_start: int,
    toc_entries: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    facts: list[dict[str, Any]] = []
    locations: dict[str, dict[str, Any]] = {}
    paragraph_index = 0

    for entry in toc_entries:
        paragraph_index += 1
        loc = f"pdf:toc:p{paragraph_index}"
        facts.append(
            {
                "location_id": loc,
                "kind": "paragraph",
                "scope": "toc",
                "role": "toc_entry",
                "text_excerpt": f"{entry.get('title')} {entry.get('page') or ''}".strip(),
                "display_heading": entry.get("display") or entry.get("title") or "",
                "paragraph_index": paragraph_index,
                "style_name": "pdf-toc",
                "raw_ooxml_ref": {
                    "page_estimate": entry.get("page"),
                    "page_scheme": entry.get("page_scheme"),
                },
            }
        )
        locations[loc] = {"location_id": loc, "kind": "paragraph", "scope": "toc"}

    for page_index in range(doc.page_count):
        if page_index == toc_page_index:
            continue
        page = doc[page_index]
        footer = _footer_logical_page(page)
        text = page.get_text("text") or ""
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or re.fullmatch(r"[\.…．]+", line):
                continue
            if page_index < body_start and not re.search(r"前\s*言|引\s*言", line):
                continue
            role, scope, display = _classify_pdf_line(line, page_index, body_start)
            if role == "paragraph" and scope not in {"foreword", "introduction"}:
                continue
            paragraph_index += 1
            loc = f"pdf:p{page_index + 1}:l{paragraph_index}"
            number, title_text = _split_number_title(display or line)
            raw = {
                "page_estimate": footer,
                "page_physical": page_index + 1,
                "page_scheme": _page_scheme_for_scope(scope),
            }
            fact = {
                "location_id": loc,
                "kind": "paragraph",
                "scope": scope,
                "role": role,
                "text_excerpt": line[:300],
                "display_heading": display or line,
                "paragraph_index": paragraph_index,
                "style_name": "pdf-line",
                "raw_ooxml_ref": raw,
                "bookmark_names": [],
                "table_page_breaks_before": 0,
                "body_tables_before": 0,
                "page_calibrated": False,
                "page_estimate_unreliable": False,
            }
            if number:
                fact["raw_ooxml_ref"]["numbering_display"] = number
            facts.append(fact)
            locations[loc] = {"location_id": loc, "kind": "paragraph", "scope": scope, "pdf_page": page_index + 1}

    return facts, locations


def _classify_pdf_line(line: str, page_index: int, body_start: int) -> tuple[str, str, str]:
    if re.match(r"^前\s*言", line):
        return "front_title", "foreword", "前言"
    if re.match(r"^引\s*言", line):
        return "front_title", "introduction", "引言"
    m = re.match(r"^([1-9]\d?)\s+([\u4e00-\u9fff].+)$", line)
    if m:
        display = f"{m.group(1)} {m.group(2)}"
        scope = _scope_for_chapter_title(m.group(2))
        return "chapter", scope, display
    m = re.match(r"^([1-9]\d?(?:\.\d+){1,4})\s+([\u4e00-\u9fff].+)$", line)
    if m:
        display = f"{m.group(1)} {m.group(2)}"
        return "clause", "other_body", display
    if re.match(r"^(——|·|[a-z]）|\d+）)", line):
        return "paragraph", "other_body", line
    return "paragraph", "other_body", line


def _scope_for_chapter_title(title: str) -> str:
    compact = re.sub(r"[\s\u3000]+", "", title)
    if "范围" in compact:
        return "scope"
    if "规范性引用文件" in compact:
        return "normative_references"
    if "术语和定义" in compact:
        return "terms_definitions"
    if "缩略语" in compact or "符号" in compact:
        return "symbols_abbreviations"
    return "other_body"


def _page_scheme_for_scope(scope: str) -> str:
    if scope in {"foreword", "introduction"}:
        return "roman_front_matter"
    return "arabic_body"


def _build_text_view(doc: Any, toc_page_index: int, body_start: int) -> str:
    parts: list[str] = []
    if toc_page_index >= 0:
        parts.append("# 目次\n")
        parts.append((doc[toc_page_index].get_text("text") or "").strip())
        parts.append("")
    for page_index in range(body_start, doc.page_count):
        text = doc[page_index].get_text("text") or ""
        if not text.strip():
            continue
        parts.append(text.strip())
        parts.append("")
    return "\n".join(parts).strip()


def _split_number_title(value: str) -> tuple[str, str]:
    text = re.sub(r"\s+", " ", value or "").strip()
    m = re.match(r"^(?P<number>\d+(?:\.\d+)*)\.?\s+(?P<title>.+)$", text)
    if not m:
        m = re.match(r"^(?P<number>\d+(?:\.\d+)+)(?P<title>\D.+)$", text)
    if m:
        number = re.sub(r"\.$", "", m.group("number"))
        return number, m.group("title").strip()
    return "", text


def _is_toc_self_title(title: str) -> bool:
    compact = re.sub(r"\s+", "", title)
    return compact in {"目次", "目录"} or compact in {"I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"}


def _roman_or_int(value: str) -> int | None:
    raw = (value or "").strip()
    if raw.isdigit():
        return int(raw)
    roman = raw.upper()
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = prev = 0
    for ch in reversed(roman):
        val = values.get(ch)
        if val is None:
            return None
        if val < prev:
            total -= val
        else:
            total += val
            prev = val
    return total or None
