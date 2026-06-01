"""Naming and cover-metadata helpers for MinerU outputs."""

from __future__ import annotations

import re
from typing import Any

from standard_document_assistant.pathing import safe_name


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


def extract_cover_metadata(middle_json: Any, markdown: str = "") -> dict[str, str]:
    text = "\n".join([extract_text_from_json(middle_json), markdown[:4000]])
    metadata = {
        "standard_number": "",
        "replaced_standard_number": "",
        "ics": "",
        "ccs": "",
        "file_code": "",
        "hierarchy_or_category": "",
        "issuing_organizations": "",
    }
    number_match = re.search(r"\b(GB|GB/T|GB/Z|GH/T|NY/T|DB\d{2}/T)\s*[\w.-]+[-—]\d{4}\b", text, re.I)
    if number_match:
        metadata["standard_number"] = number_match.group(0).replace("—", "-").strip()
        metadata["file_code"] = metadata["standard_number"].split()[0].upper()
    replaced_match = re.search(r"代替\s*([A-Z]{1,5}/?T?\s*[\w.-]+[-—]\d{4})", text, re.I)
    if replaced_match:
        metadata["replaced_standard_number"] = replaced_match.group(1).replace("—", "-").strip()
    ics_match = re.search(r"\bICS\s*([0-9]{2}(?:\.[0-9]{3}){0,2})\b", text, re.I)
    if ics_match:
        metadata["ics"] = ics_match.group(1)
    ccs_match = re.search(r"\bCCS\s*([A-Z]\s*\d{2})\b", text, re.I)
    if ccs_match:
        metadata["ccs"] = ccs_match.group(1).replace(" ", "")
    if "国家标准" in text:
        metadata["hierarchy_or_category"] = "国家标准"
    elif "行业标准" in text:
        metadata["hierarchy_or_category"] = "行业标准"
    elif "地方标准" in text:
        metadata["hierarchy_or_category"] = "地方标准"
    return metadata


def prepend_cover_info(markdown: str, cover_metadata: dict[str, str]) -> str:
    lines = []
    mapping = [
        ("standard_number", "标准正式编号"),
        ("replaced_standard_number", "代替标准"),
        ("ics", "ICS"),
        ("ccs", "CCS"),
        ("hierarchy_or_category", "标准层级"),
        ("issuing_organizations", "发布单位"),
    ]
    for key, label in mapping:
        value = str(cover_metadata.get(key, "")).strip()
        if value:
            lines.append(f"{label}：{value}")
    if not lines:
        return markdown
    return "\n".join(lines) + "\n\n" + markdown


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

