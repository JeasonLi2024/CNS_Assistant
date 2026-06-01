from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List


@dataclass
class DocumentSectionChunk:
    order: int
    scope: str
    heading: str
    text: str
    line_start: int
    line_end: int


@dataclass
class ParsedMarkdownDocument:
    file_name: str
    raw_text: str
    cover_text: str
    toc_text: str
    body_text: str
    lines: List[str]
    cover_line_start: int | None = None
    cover_line_end: int | None = None
    toc_line_start: int | None = None
    toc_line_end: int | None = None
    foreword_text: str = ""
    introduction_text: str = ""
    scope_text: str = ""
    normative_references_text: str = ""
    terms_definitions_text: str = ""
    symbols_abbreviations_text: str = ""
    other_body_text: str = ""
    appendix_text: str = ""
    index_text: str = ""
    references_text: str = ""
    end_text: str = ""
    section_chunks: List[DocumentSectionChunk] = field(default_factory=list)
    source_type: str = "markdown"
    text_view: str = ""
    format_facts: List[dict] = field(default_factory=list)
    source_locations: Dict[str, dict] = field(default_factory=dict)


def parse_markdown_file(file_path: str) -> ParsedMarkdownDocument:
    path = Path(file_path)
    raw = path.read_text(encoding="utf-8")
    return parse_markdown_text(raw, file_name=path.name)


def parse_markdown_text(raw: str, *, file_name: str) -> ParsedMarkdownDocument:
    lines = raw.splitlines()
    toc_idx = _find_toc_start(lines)

    if toc_idx >= 0:
        body_start_idx = _find_body_start_after_toc(lines, toc_idx + 1)
        cover_text = "\n".join(lines[:toc_idx]).strip()
        toc_text = "\n".join(lines[toc_idx:body_start_idx]).strip()
        cover_line_start, cover_line_end = _line_range_for_segment(lines, 0, toc_idx)
        toc_line_start, toc_line_end = _line_range_for_segment(lines, toc_idx, body_start_idx)
    else:
        body_start_idx = _find_first_body_start(lines)
        cover_text = "\n".join(lines[:body_start_idx]).strip()
        toc_text = ""
        cover_line_start, cover_line_end = _line_range_for_segment(lines, 0, body_start_idx)
        toc_line_start, toc_line_end = None, None

    body_lines = lines[body_start_idx:]
    body_chunks = _split_body_chunks(body_lines, body_start_idx + 1)
    body_sections = _aggregate_body_sections(body_chunks)
    body_text = _build_body_text(body_chunks)

    return ParsedMarkdownDocument(
        file_name=file_name,
        raw_text=raw,
        cover_text=cover_text,
        toc_text=toc_text,
        body_text=body_text,
        lines=lines,
        cover_line_start=cover_line_start,
        cover_line_end=cover_line_end,
        toc_line_start=toc_line_start,
        toc_line_end=toc_line_end,
        foreword_text=body_sections["foreword"],
        introduction_text=body_sections["introduction"],
        scope_text=body_sections["scope"],
        normative_references_text=body_sections["normative_references"],
        terms_definitions_text=body_sections["terms_definitions"],
        symbols_abbreviations_text=body_sections["symbols_abbreviations"],
        other_body_text=body_sections["other_body"],
        appendix_text=body_sections["appendix"],
        index_text=body_sections["index"],
        references_text=body_sections["references"],
        end_text=body_sections["end"],
        section_chunks=body_chunks,
        source_type="markdown",
        text_view=raw,
    )


def _find_toc_start(lines: List[str]) -> int:
    for i, line in enumerate(lines):
        content = _heading_content(line)
        if not content:
            continue
        normalized = _normalize_key(content)
        if normalized.startswith("目次") or normalized.startswith("目录"):
            return i
    return -1


def _find_body_start_after_toc(lines: List[str], start_idx: int) -> int:
    for i in range(start_idx, len(lines)):
        if _is_body_start_line(lines[i], inside_toc=True):
            if _looks_like_real_body_start_after_toc(lines, i):
                return i
    return len(lines)


def _find_first_body_start(lines: List[str]) -> int:
    for i, line in enumerate(lines):
        if _is_body_start_line(line, inside_toc=False):
            return i
    return len(lines)


def _is_body_start_line(line: str, inside_toc: bool) -> bool:
    stripped = line.strip()
    if not stripped:
        return False

    heading = _heading_content(stripped)
    if heading:
        normalized = _normalize_key(heading)
        if normalized.startswith("目次") or normalized.startswith("目录"):
            return False
        section_type = _classify_heading(heading)
        if inside_toc:
            # In TOC area, only a real section-start heading should end TOC.
            if section_type is None:
                return False
            if _is_toc_entry_heading(heading):
                return False
            return section_type in {
                "foreword",
                "introduction",
                "scope",
                "normative_references",
                "terms_definitions",
                "symbols_abbreviations",
                "appendix",
                "index",
                "references",
            }
        if section_type is not None:
            return True

    # Allow plain top-level numbered headings when not clearly TOC item.
    numbered = re.match(r"^\s*\d+(?:\.\d+)*\s+.+$", stripped)
    if numbered:
        if inside_toc:
            return False
        if _looks_like_toc_item(stripped):
            return False
        return True

    return False


def _split_body_chunks(lines: List[str], line_offset: int) -> List[DocumentSectionChunk]:
    if not lines:
        return []

    heading_starts = [i for i, line in enumerate(lines) if _is_chunk_heading(line)]
    if not heading_starts:
        text = "\n".join(lines).strip()
        if not text:
            return []
        return [
            DocumentSectionChunk(
                order=1,
                scope="other_body",
                heading="",
                text=text,
                line_start=line_offset,
                line_end=_last_non_empty_line(lines, line_offset),
            )
        ]

    if heading_starts[0] != 0:
        heading_starts = [0] + heading_starts

    chunks: List[DocumentSectionChunk] = []
    current_end_scope: str | None = None
    end_scopes = {"appendix", "index", "references"}

    for idx, start in enumerate(heading_starts):
        end = heading_starts[idx + 1] if idx + 1 < len(heading_starts) else len(lines)
        chunk_lines = lines[start:end]
        chunk_text = "\n".join(chunk_lines).strip()
        if not chunk_text:
            continue

        heading = _heading_content(chunk_lines[0]) or chunk_lines[0].strip()
        section_name = _classify_heading(heading)
        if section_name in end_scopes:
            current_end_scope = section_name
        elif section_name is None:
            section_name = current_end_scope or "other_body"
        else:
            current_end_scope = None

        chunks.append(
            DocumentSectionChunk(
                order=len(chunks) + 1,
                scope=section_name,
                heading=heading,
                text=chunk_text,
                line_start=line_offset + start,
                line_end=_last_non_empty_line(chunk_lines, line_offset + start),
            )
        )

    return chunks


def _aggregate_body_sections(chunks: List[DocumentSectionChunk]) -> Dict[str, str]:
    sections = {
        "foreword": "",
        "introduction": "",
        "scope": "",
        "normative_references": "",
        "terms_definitions": "",
        "symbols_abbreviations": "",
        "other_body": "",
        "appendix": "",
        "index": "",
        "references": "",
        "end": "",
    }

    for chunk in chunks:
        sections[chunk.scope] = _append_text(sections[chunk.scope], chunk.text)
        if chunk.scope in {"appendix", "index", "references"}:
            sections["end"] = _append_text(sections["end"], chunk.text)

    return sections


def _build_body_text(chunks: List[DocumentSectionChunk]) -> str:
    if not chunks:
        return ""

    first_scope_order = next((chunk.order for chunk in chunks if chunk.scope == "scope"), None)
    allowed_scopes = {
        "scope",
        "normative_references",
        "terms_definitions",
        "symbols_abbreviations",
        "other_body",
    }
    parts: List[str] = []
    for chunk in chunks:
        if chunk.scope not in allowed_scopes:
            continue
        if first_scope_order is not None and chunk.order < first_scope_order:
            continue
        parts.append(chunk.text)
    return "\n\n".join(parts).strip()


def _append_text(existing: str, text: str) -> str:
    if not text:
        return existing
    if not existing:
        return text
    return f"{existing}\n\n{text}".strip()


def _line_range_for_segment(lines: List[str], start_idx: int, end_idx: int) -> tuple[int | None, int | None]:
    non_empty = [i for i in range(start_idx, end_idx) if (lines[i] or "").strip()]
    if not non_empty:
        return None, None
    return non_empty[0] + 1, non_empty[-1] + 1


def _last_non_empty_line(lines: List[str], line_offset: int) -> int:
    for idx in range(len(lines) - 1, -1, -1):
        if (lines[idx] or "").strip():
            return line_offset + idx
    return line_offset


def _is_chunk_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False

    heading = _heading_content(stripped)
    if heading:
        if _classify_heading(heading) is not None:
            return True
        # Treat numbered markdown headings as major section boundaries.
        if re.match(r"^\d+(?:\.\d+)*", _normalize_key(heading)):
            return True
        # Sentence-like markdown lines inside foreword/introduction are content, not section boundaries.
        if re.search(r"[。！？；;：:]$", heading):
            return False
        return True

    return bool(re.match(r"^\d+(?:\.\d+)*\s+.+$", stripped))


def _heading_content(line: str) -> str:
    stripped = line.strip()
    m = re.match(r"^#{1,6}\s*(.+?)\s*$", stripped)
    if m:
        return m.group(1).strip()
    if stripped in {"目次", "目录", "前言", "引言", "参考文献", "索引"}:
        return stripped
    return ""


def _classify_heading(heading: str) -> str | None:
    normalized = _normalize_key(heading)
    no_number = re.sub(r"^\d+(?:\.\d+)*", "", normalized)

    if no_number.startswith("前言"):
        return "foreword"
    if no_number.startswith("引言"):
        return "introduction"
    if "规范性引用文件" in no_number:
        return "normative_references"
    if "术语和定义" in no_number:
        return "terms_definitions"
    if (
        no_number.startswith("缩略语")
        or "缩略语" in no_number
        or "符号和缩略语" in no_number
        or "符号与缩略语" in no_number
        or "符号及缩略语" in no_number
        or "符号缩略语" in no_number
        or "代号和缩略语" in no_number
        or "代号与缩略语" in no_number
    ):
        return "symbols_abbreviations"
    if no_number.startswith("范围"):
        return "scope"
    if no_number.startswith("附录"):
        return "appendix"
    if no_number.startswith("索引"):
        return "index"
    if "参考文献" in no_number:
        return "references"
    return None


def _is_toc_entry_heading(heading: str) -> bool:
    normalized = _normalize_heading(heading)
    # Example: "前言 I", "前言 1", "1 范围 1" in TOC should not start body.
    if re.search(r"\s(?:[IVXLCM]+|\d+)\s*$", normalized, flags=re.IGNORECASE):
        return True
    return False


def _looks_like_toc_item(line: str) -> bool:
    normalized = _normalize_heading(line)
    return bool(re.search(r"\s(?:[IVXLCM]+|\d+)\s*$", normalized, flags=re.IGNORECASE))


def _looks_like_real_body_start_after_toc(lines: List[str], idx: int) -> bool:
    """Guard against PDF→MD TOC rows being emitted as headings.

    If a candidate "body start" is followed mainly by TOC-like rows (page numbers /
    roman numerals / dot leaders) and no paragraph-like text, treat it as still TOC.
    """
    lookahead_limit = 30
    non_empty_seen = 0
    toc_like = 0
    paragraph_like = 0

    for j in range(idx + 1, min(len(lines), idx + 1 + lookahead_limit)):
        raw = (lines[j] or "").strip()
        if not raw:
            continue
        non_empty_seen += 1
        if non_empty_seen > 12:
            break

        heading = _heading_content(raw)
        if heading:
            normalized = _normalize_key(heading)
            if normalized.startswith("目次") or normalized.startswith("目录"):
                toc_like += 1
                continue
            if _is_toc_entry_heading(heading) or _looks_like_toc_item(heading):
                toc_like += 1
                continue
            if toc_like >= 1 and (_classify_heading(heading) is not None or re.match(r"^\d+(?:\.\d+)*", normalized)):
                return False

        if _looks_like_toc_item(raw) or re.search(r"\.{3,}\s*(?:\d+|[IVXLCM]+)\s*$", raw, flags=re.IGNORECASE):
            toc_like += 1
            continue

        if re.search(r"[。！？；;：:]$", raw) or len(raw) >= 30:
            paragraph_like += 1
            break

    if paragraph_like > 0:
        return True
    if toc_like >= 2:
        return False
    return True


def _normalize_key(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^[#\s]+", "", text)
    text = re.sub(r"[\s\u3000]+", "", text)
    text = re.sub(r"[：:，,。；;（）()\[\]【】《》\-—]+", "", text)
    return text


def _strip_toc_entry_suffix(line: str) -> str:
    """Strip trailing page numbers / TOC fillers so rows align with body headings."""
    line = line.strip()
    # Arabic page numbers: "1 范围 1", "附录 A 12"
    line = re.sub(r"\s+\d+\s*$", "", line)
    # Roman leaf numerals: "前言 II", "引言 III"
    line = re.sub(r"\s+[IVXLCM]+\s*$", "", line, flags=re.IGNORECASE)
    return line.strip()


def extract_toc_items(toc_text: str) -> List[str]:
    """Collect TOC row titles.

    MinerU/PDF pipelines often emit TOC rows as markdown headings (``# 前言 II``).
    Historically we skipped ``#`` lines entirely, which dropped 前言/引言等条目并造成
    “正文有、目次无”的假阳性。
    """
    items: List[str] = []
    for raw in toc_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            inner = _heading_content(line)
            if not inner:
                continue
            normalized = _normalize_key(inner)
            if normalized.startswith("目次") or normalized.startswith("目录"):
                continue
            line = inner

        line = _strip_toc_page_suffix(line)
        items.append(_normalize_heading(line))
    return [i for i in items if i]


def _strip_toc_page_suffix(line: str) -> str:
    line = re.sub(r"\.{3,}\s*(?:\d+|[IVXLCM]+)\s*$", "", line, flags=re.IGNORECASE).strip()
    line = re.sub(r"\s+(?:\d+|[IVXLCM]+)\s*$", "", line, flags=re.IGNORECASE).strip()
    return line


def extract_body_headings(body_text: str) -> List[str]:
    headings: List[str] = []
    for raw in body_text.splitlines():
        line = raw.strip()
        if re.match(r"^#\s*", line):
            headings.append(_normalize_heading(re.sub(r"^#\s*", "", line)))
            continue
        if re.match(r"^\d+(\.\d+)*\s+", line):
            headings.append(_normalize_heading(line))
    return [h for h in headings if h]


def build_scope_text_map(doc: ParsedMarkdownDocument) -> Dict[str, str]:
    return {
        "cover": doc.cover_text,
        "toc": doc.toc_text,
        "foreword": doc.foreword_text,
        "introduction": doc.introduction_text,
        "scope": doc.scope_text,
        "normative_references": doc.normative_references_text,
        "terms_definitions": doc.terms_definitions_text,
        "symbols_abbreviations": doc.symbols_abbreviations_text,
        "other_body": doc.other_body_text,
        "appendix": doc.appendix_text,
        "index": doc.index_text,
        "references": doc.references_text,
        "end": doc.end_text,
        "body": doc.body_text,
    }


def _normalize_heading(text: str) -> str:
    text = text.strip()
    text = re.sub(r"\s+", " ", text)
    return text
