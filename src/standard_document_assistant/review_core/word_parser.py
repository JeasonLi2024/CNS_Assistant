from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Any, Iterable

from .doc_parser import ParsedMarkdownDocument, _classify_heading, parse_markdown_text

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NS = {"w": _W_NS}


def parse_word_file(file_path: str) -> ParsedMarkdownDocument:
    """Parse a .docx into the existing text document view plus Word format facts.

    The content path intentionally remains compatible with ``ParsedMarkdownDocument``
    so the existing retrieval and LLM review stages can run unchanged. Word-specific
    formatting is stored in ``format_facts`` and can be consumed by deterministic
    format checks.
    """
    try:
        from docx import Document as DocxDocument
        from docx.document import Document as DocxDocumentType
        from docx.text.paragraph import Paragraph
    except ImportError as exc:  # pragma: no cover - depends on runtime install
        raise RuntimeError("docx_dependency_missing: 请安装 python-docx 与 lxml 后再审核 .docx 文件") from exc

    path = Path(file_path)
    if path.suffix.lower() != ".docx":
        raise ValueError("unsupported_word_format: V1 仅支持 .docx，请将旧版 .doc 另存为 .docx")

    doc: DocxDocumentType = DocxDocument(str(path))
    style_defaults = _style_defaults(doc)
    ooxml_refs = _load_ooxml_refs(path)

    text_lines: list[str] = []
    facts: list[dict[str, Any]] = []
    locations: dict[str, dict[str, Any]] = {}
    paragraph_index = 0
    current_scope = "cover"
    current_number_parts: list[str] = []

    def push_line(line: str) -> int:
        text_lines.append(line)
        return len(text_lines)

    for block in _iter_block_items(doc, Paragraph):
        if isinstance(block, Paragraph):
            paragraph_index += 1
            text = _clean_text(block.text)
            if not text:
                continue
            location_id = f"p:{paragraph_index}"
            style_name = _style_name(block.style)
            ooxml_ref = ooxml_refs.get(location_id, {})
            role = _word_struct_role(text, style_name, ooxml_ref)
            if current_scope == "toc" and role != "toc_title" and _looks_like_word_toc_entry(text, style_name):
                role = "toc_entry"
            heading_text = _heading_text_for_word_paragraph(text, style_name, role)
            if heading_text:
                normalized_heading = re.sub(r"^#+\s*", "", heading_text).strip()
                numbering_display = _clean_numbering_display(str(ooxml_ref.get("numbering_display") or ""))
                corrected_numbering_display = _correct_numbering_display(numbering_display, current_number_parts)
                if corrected_numbering_display != numbering_display:
                    ooxml_ref["numbering_display_raw"] = numbering_display
                    ooxml_ref["numbering_display"] = corrected_numbering_display
                    numbering_display = corrected_numbering_display
                if role == "toc_title":
                    current_scope = "toc"
                    display_heading = normalized_heading
                elif role == "appendix_title":
                    display_heading = _appendix_display_heading(normalized_heading, numbering_display)
                    current_scope = "appendix"
                elif role == "chapter":
                    display_heading = normalized_heading
                    if numbering_display and not _starts_with_number(display_heading):
                        display_heading = f"{numbering_display} {display_heading}"
                    current_number_parts = _number_parts_from_heading(display_heading) or current_number_parts
                    classified = _classify_heading(display_heading)
                    current_scope = classified or "other_body"
                elif role == "clause":
                    display_heading = normalized_heading
                    if numbering_display and not _starts_with_number(display_heading):
                        display_heading = f"{numbering_display} {display_heading}"
                    current_number_parts = _number_parts_from_heading(display_heading) or current_number_parts
                    current_scope = _classify_heading(display_heading) or "other_body"
                else:
                    display_heading = normalized_heading
                    explicit_parts = _number_parts_from_heading(display_heading)
                    if explicit_parts:
                        current_number_parts = explicit_parts
                    classified = _classify_heading(normalized_heading)
                    if classified:
                        current_scope = classified
                    elif re.match(r"^\d+(?:\.\d+)*\s+", normalized_heading):
                        current_scope = _classify_heading(normalized_heading) or "other_body"
                    elif role == "front_title":
                        compact = _compact(normalized_heading)
                        if compact.startswith("前言"):
                            current_scope = "foreword"
                        elif compact.startswith("引言"):
                            current_scope = "introduction"
                        elif compact.startswith("参考文献"):
                            current_scope = "references"
                        elif compact.startswith("索引"):
                            current_scope = "index"
                line_no = push_line(f"# {display_heading}")
            else:
                line_no = push_line(text)

            fact = _paragraph_fact(
                paragraph=block,
                location_id=location_id,
                paragraph_index=paragraph_index,
                scope=current_scope,
                text=text,
                role=role,
                display_heading=display_heading if heading_text else "",
                style_defaults=style_defaults,
                ooxml_ref=ooxml_ref,
            )
            facts.append(fact)
            locations[location_id] = {
                "location_id": location_id,
                "kind": "paragraph",
                "paragraph_index": paragraph_index,
                "scope": current_scope,
                "line_start": line_no,
                "line_end": line_no,
                "text_excerpt": text[:240],
                "style_name": style_name,
            }
            push_line("")
            continue

    for idx, section in enumerate(doc.sections, start=1):
        location_id = f"section:{idx}"
        fact = _section_fact(section, location_id, idx)
        facts.append(fact)
        locations[location_id] = {
            "location_id": location_id,
            "kind": "section",
            "section_index": idx,
            "scope": "full_document",
        }

    _append_header_footer_facts(doc, facts, locations)

    text_view = "\n".join(text_lines).strip()
    parsed = parse_markdown_text(text_view, file_name=path.name)
    parsed.source_type = "docx"
    parsed.text_view = text_view
    parsed.format_facts = facts
    parsed.source_locations = locations
    return parsed


def _iter_block_items(doc: Any, paragraph_cls: type) -> Iterable[Any]:
    for child in doc.element.body.iterchildren():
        if child.tag.endswith("}p"):
            yield paragraph_cls(child, doc)
        elif child.tag.endswith("}sdt"):
            for nested in child.iterchildren():
                if not nested.tag.endswith("}sdtContent"):
                    continue
                for item in nested.iterchildren():
                    if item.tag.endswith("}p"):
                        yield paragraph_cls(item, doc)


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").replace("\u3000", " ")).strip()


def _style_name(style: Any) -> str:
    return str(getattr(style, "name", "") or "").strip()


def _heading_text_for_word_paragraph(text: str, style_name: str, role: str) -> str:
    if role == "toc_entry":
        return ""
    if role in {"toc_title", "front_title", "chapter", "clause", "appendix_title"}:
        return text
    normalized_style = style_name.lower()
    if "heading" in normalized_style:
        return text
    if text in {"目次", "目录", "前言", "引言", "参考文献", "索引"}:
        return text
    if re.match(r"^(?:附录\s*[A-ZＡ-Ｚ]?|[1-9]\d?(?:\.\d+){0,4})\s+(?![-—–])\S.{0,96}$", text):
        return text
    return ""


def _word_struct_role(text: str, style_name: str, ooxml_ref: dict[str, Any]) -> str:
    compact = _compact(text)
    style_compact = _compact(style_name).lower()
    if compact in {"目次", "目录"}:
        return "toc_title"
    if style_compact.startswith("toc") or style_compact.startswith("toc"):
        return "toc_entry"
    if compact in {"前言", "引言", "参考文献", "索引"}:
        return "front_title"
    if "前言引言标题" in style_compact and compact in {"前言", "引言"}:
        return "front_title"
    if "附录标识" in style_name or str(ooxml_ref.get("numbering_display") or "").startswith("附录"):
        return "appendix_title"
    if "章标题" in style_name:
        return "chapter"
    if "条标题" in style_name:
        return "clause"
    if re.match(r"^[1-9]\d?\s+(?![-—–])\S", text):
        return "chapter"
    if re.match(r"^[1-9]\d?(?:\.\d+){1,4}\s+\S", text):
        return "clause"
    if ooxml_ref.get("numbering_level") == "0" and "章" in style_name:
        return "chapter"
    return "paragraph"


def _looks_like_word_toc_entry(text: str, style_name: str) -> bool:
    compact_style = _compact(style_name).lower()
    if compact_style.startswith("toc") or "目录" in compact_style:
        return True
    raw = re.sub(r"\s+", " ", text or "").strip()
    if re.search(r"(?:\.{2,}|\s)(?:\d+|[IVXLCM]+)\s*$", raw, flags=re.IGNORECASE):
        return True
    return False


def _starts_with_number(text: str) -> bool:
    return bool(re.match(r"^\d+(?:\.\d+)*(?:\s+|\S)", text or ""))


def _clean_numbering_display(value: str) -> str:
    text = re.sub(r"\s+", "", value or "")
    text = re.sub(r"[.)）]+$", "", text)
    return text.strip()


def _appendix_display_heading(text: str, numbering_display: str) -> str:
    number = re.sub(r"\s+", "", numbering_display or "")
    if not number:
        return text
    compact = _compact(text)
    if compact.startswith(number):
        return text
    return f"{number}{text}"


def _number_parts_from_heading(value: str) -> list[str]:
    m = re.match(r"^\s*(\d+(?:\.\d+)*)", value or "")
    if not m:
        return []
    return [part for part in m.group(1).split(".") if part]


def _correct_numbering_display(numbering_display: str, current_number_parts: list[str]) -> str:
    parts = _number_parts_from_heading(numbering_display)
    if not parts or not current_number_parts:
        return numbering_display
    if parts[0] == current_number_parts[0]:
        return numbering_display
    parent_depth = len(parts) - 1
    if parent_depth <= 0 or len(current_number_parts) < parent_depth:
        return numbering_display
    prefix = current_number_parts[:parent_depth]
    return ".".join(prefix + [parts[-1]])


def _compact(text: str) -> str:
    return re.sub(r"[\s\u3000]+", "", text or "")


def _style_defaults(doc: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "normal_font_name": None,
        "normal_font_size_pt": None,
    }
    try:
        normal = doc.styles["Normal"]
    except Exception:
        normal = None
    if normal is not None:
        defaults["normal_font_name"] = getattr(normal.font, "name", None)
        defaults["normal_font_size_pt"] = _pt(getattr(normal.font, "size", None))
    return defaults


def _paragraph_fact(
    *,
    paragraph: Any,
    location_id: str,
    paragraph_index: int,
    scope: str,
    text: str,
    role: str,
    display_heading: str,
    style_defaults: dict[str, Any],
    ooxml_ref: dict[str, Any],
) -> dict[str, Any]:
    fmt = paragraph.paragraph_format
    style_name = _style_name(paragraph.style)
    return {
        "location_id": location_id,
        "kind": "paragraph",
        "scope": scope,
        "text_excerpt": text[:300],
        "style_name": style_name,
        "role": role,
        "display_heading": display_heading,
        "paragraph_index": paragraph_index,
        "paragraph": {
            "alignment": _enum_name(getattr(paragraph, "alignment", None)),
            "left_indent_pt": _pt(getattr(fmt, "left_indent", None)),
            "first_line_indent_pt": _pt(getattr(fmt, "first_line_indent", None)),
            "space_before_pt": _pt(getattr(fmt, "space_before", None)),
            "space_after_pt": _pt(getattr(fmt, "space_after", None)),
            "line_spacing": _line_spacing(getattr(fmt, "line_spacing", None)),
            "keep_together": getattr(fmt, "keep_together", None),
            "keep_with_next": getattr(fmt, "keep_with_next", None),
            "page_break_before": getattr(fmt, "page_break_before", None),
        },
        "runs": [
            _run_fact(run, style_defaults=style_defaults)
            for run in paragraph.runs
            if _clean_text(getattr(run, "text", ""))
        ][:20],
        "raw_ooxml_ref": ooxml_ref,
    }


def _run_fact(run: Any, *, style_defaults: dict[str, Any]) -> dict[str, Any]:
    font = run.font
    run_text = _clean_text(getattr(run, "text", ""))
    style_font = getattr(getattr(run, "style", None), "font", None)
    paragraph_style_font = None
    try:
        paragraph_style_font = run._parent.style.font
    except Exception:
        paragraph_style_font = None

    direct_fonts = _rfonts_from_element(getattr(run, "_element", None))
    style_fonts = _rfonts_from_element(getattr(getattr(run, "style", None), "_element", None))
    para_style_fonts = _rfonts_from_element(getattr(getattr(run._parent, "style", None), "_element", None))

    return {
        "text_excerpt": run_text[:120],
        "font_name": _first_non_empty(
            getattr(font, "name", None),
            getattr(style_font, "name", None) if style_font is not None else None,
            getattr(paragraph_style_font, "name", None) if paragraph_style_font is not None else None,
            style_defaults.get("normal_font_name"),
        ),
        "east_asia_font": _first_non_empty(
            direct_fonts.get("eastAsia"),
            style_fonts.get("eastAsia"),
            para_style_fonts.get("eastAsia"),
        ),
        "ascii_font": _first_non_empty(
            direct_fonts.get("ascii"),
            style_fonts.get("ascii"),
            para_style_fonts.get("ascii"),
        ),
        "font_size_pt": _first_non_empty(
            _pt(getattr(font, "size", None)),
            _pt(getattr(style_font, "size", None)) if style_font is not None else None,
            _pt(getattr(paragraph_style_font, "size", None)) if paragraph_style_font is not None else None,
            style_defaults.get("normal_font_size_pt"),
        ),
        "bold": _first_non_empty(getattr(font, "bold", None), getattr(style_font, "bold", None) if style_font else None),
        "italic": _first_non_empty(getattr(font, "italic", None), getattr(style_font, "italic", None) if style_font else None),
        "underline": bool(getattr(font, "underline", False) or False),
        "superscript": getattr(font, "superscript", None),
        "subscript": getattr(font, "subscript", None),
    }


def _section_fact(section: Any, location_id: str, section_index: int) -> dict[str, Any]:
    return {
        "location_id": location_id,
        "kind": "section",
        "scope": "full_document",
        "text_excerpt": "",
        "section": {
            "section_index": section_index,
            "page_width_cm": _cm(getattr(section, "page_width", None)),
            "page_height_cm": _cm(getattr(section, "page_height", None)),
            "orientation": _enum_name(getattr(section, "orientation", None)),
            "top_margin_cm": _cm(getattr(section, "top_margin", None)),
            "bottom_margin_cm": _cm(getattr(section, "bottom_margin", None)),
            "left_margin_cm": _cm(getattr(section, "left_margin", None)),
            "right_margin_cm": _cm(getattr(section, "right_margin", None)),
            "header_distance_cm": _cm(getattr(section, "header_distance", None)),
            "footer_distance_cm": _cm(getattr(section, "footer_distance", None)),
        },
    }


def _append_header_footer_facts(doc: Any, facts: list[dict[str, Any]], locations: dict[str, dict[str, Any]]) -> None:
    for section_index, section in enumerate(doc.sections, start=1):
        for part_name in ("header", "footer"):
            part = getattr(section, part_name, None)
            if part is None:
                continue
            for paragraph_index, paragraph in enumerate(part.paragraphs, start=1):
                text = _clean_text(paragraph.text)
                if not text:
                    continue
                loc = f"{part_name}:{section_index}:p{paragraph_index}"
                fact = {
                    "location_id": loc,
                    "kind": part_name,
                    "scope": "full_document",
                    "text_excerpt": text[:300],
                    "style_name": _style_name(paragraph.style),
                    "paragraph": {
                        "alignment": _enum_name(getattr(paragraph, "alignment", None)),
                    },
                    "runs": [_run_fact(run, style_defaults={}) for run in paragraph.runs if _clean_text(run.text)][:20],
                }
                facts.append(fact)
                locations[loc] = {
                    "location_id": loc,
                    "kind": part_name,
                    "section_index": section_index,
                    "paragraph_index": paragraph_index,
                    "scope": "full_document",
                    "text_excerpt": text[:240],
                }


def _load_ooxml_refs(path: Path) -> dict[str, dict[str, Any]]:
    refs: dict[str, dict[str, Any]] = {}
    try:
        from lxml import etree
    except ImportError:
        return refs
    try:
        with zipfile.ZipFile(path) as zf:
            raw = zf.read("word/document.xml")
            numbering_raw = zf.read("word/numbering.xml") if "word/numbering.xml" in zf.namelist() else b""
            styles_raw = zf.read("word/styles.xml") if "word/styles.xml" in zf.namelist() else b""
    except Exception:
        return refs
    try:
        root = etree.fromstring(raw)
    except Exception:
        return refs
    numbering = _load_numbering_model(etree, numbering_raw)
    style_numbering = _load_style_numbering(etree, styles_raw)
    numbering_state: dict[str, dict[int, int]] = {}
    page = 1
    mapped_idx = 0
    table_page_breaks_seen = 0
    body_tables_seen = 0
    for paragraph, mapped, xpath, table_delta in _iter_ooxml_body_paragraph_events(root):
        if table_delta:
            body_tables_seen += table_delta
        breaks_before = len(paragraph.xpath(".//w:lastRenderedPageBreak", namespaces=_NS))
        if breaks_before:
            # 表格内分页断点不计入正文段 page_estimate，避免抬高后续标题的估算页码。
            if mapped:
                page += breaks_before
            else:
                table_page_breaks_seen += breaks_before
        if not mapped:
            continue
        mapped_idx += 1
        style_id = _child_attr(paragraph, "w:pPr/w:pStyle", "val")
        direct_num_id = _child_attr(paragraph, "w:pPr/w:numPr/w:numId", "val")
        direct_level = _child_attr(paragraph, "w:pPr/w:numPr/w:ilvl", "val")
        style_num = style_numbering.get(style_id or "", {})
        numbering_id = direct_num_id or style_num.get("numbering_id")
        numbering_level = direct_level or style_num.get("numbering_level")
        numbering_display = _compute_numbering_display(
            numbering=numbering,
            state=numbering_state,
            num_id=numbering_id,
            ilvl=numbering_level,
        )
        bookmark_names = [
            str(node.get(f"{{{_W_NS}}}name") or "")
            for node in paragraph.xpath(".//w:bookmarkStart", namespaces=_NS)
            if str(node.get(f"{{{_W_NS}}}name") or "")
        ]
        instr_text = " ".join(
            str(node.text or "").strip()
            for node in paragraph.xpath(".//w:instrText", namespaces=_NS)
            if str(node.text or "").strip()
        )
        refs[f"p:{mapped_idx}"] = {
            "xpath": xpath,
            "style_id": style_id,
            "numbering_id": numbering_id,
            "numbering_level": numbering_level,
            "numbering_display": numbering_display,
            "page_estimate": page,
            "table_page_breaks_before": table_page_breaks_seen,
            "body_tables_before": body_tables_seen,
            "bookmark_names": bookmark_names,
            "field_instr": instr_text,
        }
    return refs


def _load_numbering_model(etree: Any, numbering_raw: bytes) -> dict[str, Any]:
    if not numbering_raw:
        return {"nums": {}, "abstracts": {}}
    try:
        root = etree.fromstring(numbering_raw)
    except Exception:
        return {"nums": {}, "abstracts": {}}
    abstracts: dict[str, dict[int, dict[str, Any]]] = {}
    for abstract in root.xpath(".//w:abstractNum", namespaces=_NS):
        abstract_id = str(abstract.get(f"{{{_W_NS}}}abstractNumId") or "")
        levels: dict[int, dict[str, Any]] = {}
        for lvl in abstract.xpath("./w:lvl", namespaces=_NS):
            ilvl = _safe_int(lvl.get(f"{{{_W_NS}}}ilvl"))
            if ilvl is None:
                continue
            levels[ilvl] = {
                "start": _safe_int(_child_attr(lvl, "w:start", "val")) or 1,
                "numFmt": _child_attr(lvl, "w:numFmt", "val") or "decimal",
                "lvlText": _child_attr(lvl, "w:lvlText", "val") or f"%{ilvl + 1}",
            }
        abstracts[abstract_id] = levels
    nums: dict[str, dict[str, Any]] = {}
    for num in root.xpath(".//w:num", namespaces=_NS):
        num_id = str(num.get(f"{{{_W_NS}}}numId") or "")
        abstract_id = _child_attr(num, "w:abstractNumId", "val")
        overrides: dict[int, dict[str, Any]] = {}
        for override in num.xpath("./w:lvlOverride", namespaces=_NS):
            ilvl = _safe_int(override.get(f"{{{_W_NS}}}ilvl"))
            if ilvl is None:
                continue
            start = _safe_int(_child_attr(override, "w:startOverride", "val"))
            lvl_nodes = override.xpath("./w:lvl", namespaces=_NS)
            data: dict[str, Any] = {}
            if start is not None:
                data["start"] = start
            if lvl_nodes:
                data.update({
                    "numFmt": _child_attr(lvl_nodes[0], "w:numFmt", "val") or "decimal",
                    "lvlText": _child_attr(lvl_nodes[0], "w:lvlText", "val") or f"%{ilvl + 1}",
                })
            overrides[ilvl] = data
        if num_id and abstract_id:
            nums[num_id] = {"abstract_id": abstract_id, "overrides": overrides}
    return {"nums": nums, "abstracts": abstracts}


def _load_style_numbering(etree: Any, styles_raw: bytes) -> dict[str, dict[str, str]]:
    if not styles_raw:
        return {}
    try:
        root = etree.fromstring(styles_raw)
    except Exception:
        return {}
    direct: dict[str, dict[str, str]] = {}
    based_on: dict[str, str] = {}
    for style in root.xpath(".//w:style", namespaces=_NS):
        style_id = str(style.get(f"{{{_W_NS}}}styleId") or "")
        base = _child_attr(style, "w:basedOn", "val")
        if style_id and base:
            based_on[style_id] = base
        num_id = _child_attr(style, "w:pPr/w:numPr/w:numId", "val")
        ilvl = _child_attr(style, "w:pPr/w:numPr/w:ilvl", "val")
        if style_id and (num_id or ilvl):
            direct[style_id] = {}
            if num_id:
                direct[style_id]["numbering_id"] = num_id
                direct[style_id]["numbering_level"] = ilvl or "0"
            elif ilvl:
                direct[style_id]["numbering_level"] = ilvl

    resolved: dict[str, dict[str, str]] = {}

    def resolve(style_id: str, seen: set[str] | None = None) -> dict[str, str]:
        if style_id in resolved:
            return resolved[style_id]
        seen = seen or set()
        if style_id in seen:
            return {}
        seen.add(style_id)
        parent = resolve(based_on[style_id], seen) if style_id in based_on else {}
        current = dict(parent)
        current.update(direct.get(style_id) or {})
        if current:
            resolved[style_id] = current
        return current

    for style_id in set(direct) | set(based_on):
        resolve(style_id)
    out = {
        style_id: data
        for style_id, data in resolved.items()
        if data.get("numbering_id") and data.get("numbering_level") is not None
    }
    return out


def _compute_numbering_display(
    *,
    numbering: dict[str, Any],
    state: dict[str, dict[int, int]],
    num_id: str | None,
    ilvl: str | None,
) -> str:
    if not num_id or ilvl is None:
        return ""
    level = _safe_int(ilvl)
    if level is None:
        return ""
    num_def = numbering.get("nums", {}).get(str(num_id))
    if not num_def:
        return ""
    abstract_levels = numbering.get("abstracts", {}).get(str(num_def.get("abstract_id")), {})
    lvl_def = _merged_level_def(abstract_levels, num_def.get("overrides", {}), level)
    if not lvl_def:
        return ""
    counters = state.setdefault(str(num_id), {})
    for lower in list(counters):
        if lower > level:
            counters.pop(lower, None)
    for missing in range(0, level):
        if missing not in counters:
            missing_def = _merged_level_def(abstract_levels, num_def.get("overrides", {}), missing)
            counters[missing] = int((missing_def or {}).get("start") or 1)
    counters[level] = counters.get(level, int(lvl_def.get("start") or 1) - 1) + 1
    text = str(lvl_def.get("lvlText") or f"%{level + 1}")
    for i in range(0, 9):
        token = f"%{i + 1}"
        value = counters.get(i)
        level_def = _merged_level_def(abstract_levels, num_def.get("overrides", {}), i) or {}
        replacement = "" if value is None else _format_number(value, str(level_def.get("numFmt") or "decimal"))
        text = text.replace(token, replacement)
    return _clean_numbering_display(text)


def _merged_level_def(
    abstract_levels: dict[int, dict[str, Any]],
    overrides: dict[int, dict[str, Any]],
    level: int,
) -> dict[str, Any]:
    base = dict(abstract_levels.get(level) or {})
    base.update(overrides.get(level) or {})
    return base


def _format_number(value: int, num_fmt: str) -> str:
    if num_fmt == "lowerLetter":
        return _alpha_number(value).lower()
    if num_fmt == "upperLetter":
        return _alpha_number(value).upper()
    if num_fmt == "lowerRoman":
        return _roman_number(value).lower()
    if num_fmt == "upperRoman":
        return _roman_number(value).upper()
    return str(value)


def _alpha_number(value: int) -> str:
    if value <= 0:
        return str(value)
    chars: list[str] = []
    n = value
    while n:
        n -= 1
        chars.append(chr(ord("A") + (n % 26)))
        n //= 26
    return "".join(reversed(chars))


def _roman_number(value: int) -> str:
    if value <= 0:
        return str(value)
    pairs = [
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
        (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
        (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
    ]
    out: list[str] = []
    n = value
    for val, glyph in pairs:
        while n >= val:
            out.append(glyph)
            n -= val
    return "".join(out)


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _iter_ooxml_body_paragraph_events(root: Any) -> Iterable[tuple[Any, bool, str, int]]:
    body = root.find("w:body", namespaces=_NS)
    if body is None:
        return
    mapped_seq = 0
    body_seq = 0
    for child in body.iterchildren():
        if child.tag == f"{{{_W_NS}}}p":
            body_seq += 1
            mapped_seq += 1
            yield child, True, f"/w:document/w:body/*[{body_seq}]", 0
        elif child.tag == f"{{{_W_NS}}}sdt":
            body_seq += 1
            for nested in child.iterchildren():
                if nested.tag != f"{{{_W_NS}}}sdtContent":
                    continue
                content_seq = 0
                for item in nested.iterchildren():
                    content_seq += 1
                    if item.tag == f"{{{_W_NS}}}p":
                        mapped_seq += 1
                        yield item, True, f"/w:document/w:body/*[{body_seq}]/w:sdtContent/*[{content_seq}]", 0
                    elif item.tag == f"{{{_W_NS}}}tbl":
                        yield from _iter_table_paragraph_events(
                            item,
                            f"/w:document/w:body/*[{body_seq}]/w:sdtContent/*[{content_seq}]",
                        )
        elif child.tag == f"{{{_W_NS}}}tbl":
            body_seq += 1
            yield from _iter_table_paragraph_events(child, f"/w:document/w:body/*[{body_seq}]")


def _iter_table_paragraph_events(table: Any, base_xpath: str) -> Iterable[tuple[Any, bool, str, int]]:
    para_seq = 0
    for paragraph in table.xpath(".//w:p", namespaces=_NS):
        para_seq += 1
        yield paragraph, False, f"{base_xpath}//w:p[{para_seq}]", 1 if para_seq == 1 else 0


def _child_attr(node: Any, xpath: str, attr_local: str) -> str | None:
    found = node.xpath(xpath, namespaces=_NS)
    if not found:
        return None
    return found[0].get(f"{{{_W_NS}}}{attr_local}")


def _rfonts_from_element(element: Any) -> dict[str, str | None]:
    if element is None:
        return {}
    try:
        rfonts = element.xpath(".//w:rFonts", namespaces=_NS)
    except TypeError:
        try:
            rfonts = element.xpath(".//w:rFonts")
        except Exception:
            rfonts = []
    except Exception:
        rfonts = []
    if not rfonts:
        return {}
    node = rfonts[0]
    return {
        "ascii": node.get(f"{{{_W_NS}}}ascii"),
        "eastAsia": node.get(f"{{{_W_NS}}}eastAsia"),
        "hAnsi": node.get(f"{{{_W_NS}}}hAnsi"),
    }


def _pt(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value.pt), 2)
    except Exception:
        return None


def _cm(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value.cm), 3)
    except Exception:
        return None


def _line_spacing(value: Any) -> float | str | None:
    if value is None:
        return None
    try:
        return round(float(value), 3)
    except Exception:
        return str(value)


def _enum_name(value: Any) -> str | None:
    if value is None:
        return None
    name = getattr(value, "name", None)
    if name:
        return str(name)
    return str(value)


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None
