"""Naming and cover-metadata helpers for MinerU outputs."""

from __future__ import annotations

import re
from typing import Any

from standard_document_assistant.pathing import safe_name

_COVER_METADATA_KEYS = (
    "standard_number",
    "replaced_standard_number",
    "ics",
    "ccs",
    "file_code",
    "hierarchy_or_category",
    "issuing_organizations",
)


def extract_text_from_json(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        parts: list[str] = []
        for key in ("text", "content", "html"):
            if isinstance(value.get(key), str):
                parts.append(value[key])
        for key in ("spans", "lines", "blocks"):
            child = value.get(key)
            if isinstance(child, list):
                parts.extend(extract_text_from_json(item) for item in child)
        return "\n".join(part for part in parts if part)
    if isinstance(value, list):
        return "\n".join(extract_text_from_json(item) for item in value)
    return ""


def has_pdf_info_payload(value: Any) -> bool:
    return isinstance(value, dict) and isinstance(value.get("pdf_info"), list)


def _empty_cover_metadata() -> dict[str, str]:
    return {key: "" for key in _COVER_METADATA_KEYS}


def _normalize_standard_number(text: str) -> str:
    return text.replace("—", "-").strip()


def _extract_header_text(block: dict[str, Any]) -> str:
    parts: list[str] = []
    for line in block.get("lines", []) or []:
        if not isinstance(line, dict):
            continue
        for span in line.get("spans", []) or []:
            if not isinstance(span, dict):
                continue
            content = span.get("content")
            if not content:
                continue
            if isinstance(content, str):
                parts.append(content.strip())
            elif isinstance(content, dict):
                text_value = content.get("text") or content.get("content") or ""
                if text_value:
                    parts.append(str(text_value).strip())
            else:
                parts.append(str(content).strip())
    return "".join(parts).strip()


def _extract_cover_metadata_from_pdf_info(middle_json: dict[str, Any]) -> dict[str, str]:
    """从 middle_json / layout.json 第 0 页 discarded_blocks 抽取封面元信息。"""
    metadata = _empty_cover_metadata()
    if not isinstance(middle_json, dict):
        return metadata

    for page in middle_json.get("pdf_info", []) or []:
        if page.get("page_idx") != 0:
            continue

        issuing_orgs: list[str] = []
        header_candidates: list[dict[str, Any]] = []

        def _clean(text: str) -> str:
            return re.sub(r"\s+", " ", (text or "")).strip()

        def _is_ics(text: str) -> bool:
            t = _clean(text).upper()
            return t.startswith("ICS") or "国际标准分类" in text

        def _is_ccs(text: str) -> bool:
            t = _clean(text).upper()
            if t.startswith("CCS") or "中国标准文献分类" in text:
                return True
            compact = t.replace(" ", "")
            return bool(re.fullmatch(r"[A-Z]{1,3}\d{1,3}(?:\.\d+)?", compact))

        def _is_replaced_standard(text: str) -> bool:
            return "代替" in text or "替代" in text

        def _extract_standard_number_by_format(text: str) -> str:
            original = _clean(text)
            for replace_prefix in ("代替", "替代"):
                if original.startswith(replace_prefix):
                    original = original[len(replace_prefix) :].strip()
            if not original:
                return ""
            check_text = original.upper()
            prefix = r"(?:[A-Z]{1,6}\d{0,3}(?:/[A-Z]{1,8}\d{0,3})?)\s+\d+(?:\.\d+)*"
            year = r"(?:\d{4}|\d{2})"
            suffix = r"(?:.*)?"
            if re.fullmatch(rf"{prefix}—{year}{suffix}", check_text):
                return _normalize_standard_number(original)
            if re.fullmatch(rf"{prefix}-{year}{suffix}", check_text):
                return _normalize_standard_number(original)
            return ""

        def _is_hierarchy_or_category(text: str) -> bool:
            keywords = [
                "中华人民共和国国家标准",
                "国家标准化指导性技术文件",
                "行业标准",
                "地方标准",
            ]
            return any(keyword in text for keyword in keywords)

        def _is_standard_number(text: str) -> bool:
            return bool(_extract_standard_number_by_format(text))

        def _is_file_code(text: str) -> bool:
            t = _clean(text).upper().replace(" ", "")
            if not t or _is_ics(text) or _is_ccs(text):
                return False
            return bool(re.fullmatch(r"[A-Z]{1,6}(?:/[A-Z]{1,6})?", t))

        for block in page.get("discarded_blocks", []) or []:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            block_text = _extract_header_text(block)
            if not block_text:
                continue
            if block_type == "header":
                header_candidates.append({"index": block.get("index"), "text": _clean(block_text)})
            elif block_type == "footer":
                normalized_footer = block_text.strip()
                if normalized_footer == "发布":
                    continue
                if normalized_footer.endswith("发布"):
                    normalized_footer = normalized_footer[:-2].strip()
                if normalized_footer:
                    issuing_orgs.append(normalized_footer)

        for item in header_candidates:
            text = item["text"]
            if not text:
                continue
            if _is_ics(text) and not metadata["ics"]:
                metadata["ics"] = text
                continue
            if _is_ccs(text) and not metadata["ccs"]:
                metadata["ccs"] = text
                continue
            if _is_replaced_standard(text) and not metadata["replaced_standard_number"]:
                extracted = _extract_standard_number_by_format(text)
                metadata["replaced_standard_number"] = extracted if extracted else text
                continue
            if _is_standard_number(text) and not metadata["standard_number"]:
                metadata["standard_number"] = _extract_standard_number_by_format(text)
                continue
            if _is_hierarchy_or_category(text) and not metadata["hierarchy_or_category"]:
                metadata["hierarchy_or_category"] = text
                continue
            if _is_file_code(text) and not metadata["file_code"]:
                metadata["file_code"] = text

        if not metadata["standard_number"] or not metadata["replaced_standard_number"]:
            for item in header_candidates:
                text = item["text"]
                if not text:
                    continue
                extracted = _extract_standard_number_by_format(text)
                if not extracted:
                    continue
                if _is_replaced_standard(text) and not metadata["replaced_standard_number"]:
                    metadata["replaced_standard_number"] = extracted
                    continue
                if not metadata["standard_number"] and not _is_replaced_standard(text):
                    metadata["standard_number"] = extracted

        index_to_field = {
            0: "ics",
            1: "ccs",
            2: "file_code",
            3: "hierarchy_or_category",
            4: "standard_number",
            5: "replaced_standard_number",
        }
        header_text_by_index: dict[int, str] = {}
        for candidate in header_candidates:
            candidate_idx = candidate.get("index")
            if candidate_idx not in header_text_by_index and isinstance(candidate_idx, int):
                header_text_by_index[candidate_idx] = candidate.get("text", "")

        for item in header_candidates:
            idx = item.get("index")
            text = item["text"]
            if idx not in index_to_field:
                continue
            field_name = index_to_field[idx]
            if metadata.get(field_name):
                continue
            extracted_standard_number = _extract_standard_number_by_format(text)
            if field_name == "standard_number":
                if extracted_standard_number and not _is_replaced_standard(text):
                    metadata[field_name] = extracted_standard_number
                elif idx == 4:
                    text_idx3 = header_text_by_index.get(3, "")
                    extracted_idx3 = _extract_standard_number_by_format(text_idx3)
                    if extracted_idx3 and not _is_replaced_standard(text_idx3):
                        metadata[field_name] = extracted_idx3
                continue
            if field_name == "replaced_standard_number":
                if _is_replaced_standard(text):
                    metadata[field_name] = (
                        extracted_standard_number if extracted_standard_number else text
                    )
                continue
            if field_name == "file_code" and (
                _is_ics(text) or _is_ccs(text) or _is_standard_number(text)
            ):
                continue
            if field_name == "ics" and _is_ccs(text):
                continue
            if field_name == "ccs" and _is_ics(text):
                continue
            metadata[field_name] = text

        if issuing_orgs:
            metadata["issuing_organizations"] = " ".join(issuing_orgs)
        break

    if metadata["hierarchy_or_category"] and "国家标准" in metadata["hierarchy_or_category"]:
        metadata["hierarchy_or_category"] = "国家标准"
    elif metadata["hierarchy_or_category"] and "行业标准" in metadata["hierarchy_or_category"]:
        metadata["hierarchy_or_category"] = "行业标准"
    elif metadata["hierarchy_or_category"] and "地方标准" in metadata["hierarchy_or_category"]:
        metadata["hierarchy_or_category"] = "地方标准"

    return metadata


_STANDARD_NUMBER_PATTERN = re.compile(
    r"\b(GB|GB/T|GB/Z|GH/T|NY/T|DB\d{2}/T)\s*[\w.-]+[-—]\d{4}\b",
    re.I,
)


def _extract_cover_metadata_from_text(text: str) -> dict[str, str]:
    """从全文（content_list 等）回退抽取；跳过含「代替」的匹配作为正式编号。"""
    metadata = _empty_cover_metadata()
    if not text:
        return metadata

    for match in _STANDARD_NUMBER_PATTERN.finditer(text):
        context = text[max(0, match.start() - 20) : match.end() + 20]
        if re.search(r"代替|替代|相比|首次发布|被代替|废止", context):
            continue
        metadata["standard_number"] = _normalize_standard_number(match.group(0))
        break

    replaced_match = re.search(
        r"代替\s*([A-Z]{1,5}/?T?\s*[\w.-]+[-—]\d{4})",
        text,
        re.I,
    )
    if replaced_match:
        metadata["replaced_standard_number"] = _normalize_standard_number(replaced_match.group(1))

    ics_match = re.search(r"\bICS\s*([0-9]{2}(?:\.[0-9]+){0,2})\b", text, re.I)
    if ics_match:
        metadata["ics"] = ics_match.group(1)
    ccs_match = re.search(r"\bCCS\s*([A-Z]\s*\d{2})\b", text, re.I)
    if ccs_match:
        metadata["ccs"] = ccs_match.group(1).replace(" ", "")
    elif re.search(r"\bB\s*31\b", text):
        ccs_line = re.search(r"\b([A-Z])\s+(\d{2})\b", text)
        if ccs_line and ccs_line.group(1) in {"A", "B", "C", "D", "E", "F", "G", "H"}:
            metadata["ccs"] = f"{ccs_line.group(1)}{ccs_line.group(2)}"

    file_code_match = re.search(
        r"(?:^|\n)\s*(GB|GB/T|GB/Z|DB\d{2}/T)\s*(?:\n|$)",
        text,
        re.I | re.M,
    )
    if file_code_match and not metadata["file_code"]:
        metadata["file_code"] = file_code_match.group(1).upper()

    if "国家标准" in text:
        metadata["hierarchy_or_category"] = "国家标准"
    elif "行业标准" in text:
        metadata["hierarchy_or_category"] = "行业标准"
    elif "地方标准" in text:
        metadata["hierarchy_or_category"] = "地方标准"

    if metadata["standard_number"] and not metadata["file_code"]:
        number_parts = metadata["standard_number"].upper().split()
        if number_parts:
            metadata["file_code"] = number_parts[0].split("/")[0]

    return metadata


def _merge_cover_metadata(primary: dict[str, str], fallback: dict[str, str]) -> dict[str, str]:
    merged = dict(primary)
    for key in _COVER_METADATA_KEYS:
        if not str(merged.get(key, "")).strip() and str(fallback.get(key, "")).strip():
            merged[key] = fallback[key]
    return merged


def extract_cover_metadata(
    middle_json: Any,
    markdown: str = "",
    *,
    layout_json: Any = None,
    content_list: Any = None,
) -> dict[str, str]:
    """抽取封面元信息：优先 pdf_info 结构化解析，再回退 content_list / Markdown 文本。"""

    structured_sources = [middle_json, layout_json]
    metadata = _empty_cover_metadata()
    for source in structured_sources:
        if not has_pdf_info_payload(source):
            continue
        extracted = _extract_cover_metadata_from_pdf_info(source)
        metadata = _merge_cover_metadata(extracted, metadata)
        if metadata["standard_number"]:
            break

    text_sources = [
        extract_text_from_json(content_list),
        extract_text_from_json(middle_json) if not has_pdf_info_payload(middle_json) else "",
        extract_text_from_json(layout_json) if not has_pdf_info_payload(layout_json) else "",
        markdown[:8000],
    ]
    fallback_text = "\n".join(part for part in text_sources if part)
    if fallback_text.strip():
        metadata = _merge_cover_metadata(metadata, _extract_cover_metadata_from_text(fallback_text))

    return metadata


def prepend_cover_info(markdown: str, cover_metadata: dict[str, str]) -> str:
    """将封面元信息写入 Markdown 头部（字段标签与 minerU2_2 对齐）。"""

    lines: list[str] = []
    standard_number = str(cover_metadata.get("standard_number", "")).strip()
    replaced_standard_number = str(cover_metadata.get("replaced_standard_number", "")).strip()

    if standard_number:
        lines.append(f"标准正式编号：{standard_number}")
    if replaced_standard_number:
        if replaced_standard_number.startswith("代替"):
            lines.append(replaced_standard_number)
        else:
            lines.append(f"代替{replaced_standard_number}")

    for key, label in (
        ("ics", "ICS"),
        ("ccs", "CCS"),
        ("file_code", "文件代号"),
        ("hierarchy_or_category", "文件的层次或类别"),
        ("issuing_organizations", "发布机构"),
    ):
        value = str(cover_metadata.get(key, "")).strip()
        if value:
            lines.append(f"{label}：{value}")

    if not lines:
        return markdown
    return "\n\n".join(lines) + "\n\n" + markdown


def markdown_base_name(source_stem: str, cover_metadata: dict[str, str]) -> str:
    standard_number = str(cover_metadata.get("standard_number", "")).strip()
    if standard_number:
        return safe_name(standard_number.replace("/", "-").replace(" ", "-"))
    return safe_name(source_stem)


def markdown_category(cover_metadata: dict[str, str]) -> str:
    number = str(cover_metadata.get("standard_number", "")).upper().replace(" ", "")
    hierarchy = str(cover_metadata.get("hierarchy_or_category", ""))
    if number.startswith("GB") or "国家标准" in hierarchy:
        return "国家标准"
    if re.match(r"DB\d{2}", number) or "地方标准" in hierarchy:
        return "地方标准"
    if number or "行业标准" in hierarchy:
        return "行业标准"
    return "其他"
