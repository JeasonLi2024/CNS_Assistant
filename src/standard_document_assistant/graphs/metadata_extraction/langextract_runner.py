"""Langextract adapter with a deterministic fallback."""

from __future__ import annotations

import re
from typing import Any


def run_extraction(text: str) -> dict[str, Any]:
    """Return raw extraction data.

    The production path can be extended to call langextract. The deterministic
    fallback keeps local tests and smoke runs independent from external LLMs.
    """

    return heuristic_extract(text)


def heuristic_extract(text: str) -> dict[str, Any]:
    def first(pattern: str, flags: int = re.I | re.M) -> str:
        match = re.search(pattern, text, flags)
        return match.group(1).strip() if match else ""

    headings = [
        line.lstrip("#").strip()
        for line in text.splitlines()
        if line.strip().startswith("#")
    ]
    title = headings[0] if headings else first(r"标准中文名称[：:]\s*(.+)")
    standard_number = first(r"(?:标准正式编号|标准号)[：:]\s*([^\n]+)")
    if not standard_number:
        match = re.search(r"\b(GB|GB/T|GB/Z|GH/T|NY/T|DB\d{2}/T)\s*[\w.-]+[-—]\d{4}\b", text, re.I)
        standard_number = match.group(0).replace("—", "-").strip() if match else ""
    references = _section_items(text, ["规范性引用文件", "引用文件"], stop_prefixes=["术语", "3 ", "## 3"])
    terms = _section_items(text, ["术语和定义", "术语"], stop_prefixes=["4 ", "## 4"])
    return {
        "ics": first(r"\bICS\s*([0-9]{2}(?:\.[0-9]{3}){0,2})\b"),
        "ccs": first(r"\bCCS\s*([A-Z]\s*\d{2})\b").replace(" ", ""),
        "标准层级": _infer_level(text, standard_number),
        "标准号": standard_number,
        "代替标准号": first(r"代替\s*([A-Z]{1,5}/?T?\s*[\w.-]+[-—]\d{4})"),
        "发布日期": first(r"(?:发布|发布日期)[：:\s]*([0-9]{4}[-年][0-9]{1,2}[-月][0-9]{1,2}日?)"),
        "实施日期": first(r"(?:实施|实施日期)[：:\s]*([0-9]{4}[-年][0-9]{1,2}[-月][0-9]{1,2}日?)"),
        "标准中文名称": title,
        "标准英文名称": first(r"(?:英文名称|English name)[：:]\s*([^\n]+)", re.I),
        "采标信息": first(r"(?:采标|采用国际标准)[：:]\s*([^\n]+)"),
        "提出单位": _split_orgs(first(r"提出单位[：:]\s*([^\n]+)")),
        "归口单位": _split_orgs(first(r"归口单位[：:]\s*([^\n]+)")),
        "起草单位": _split_orgs(first(r"起草单位[：:]\s*([^\n]+)")),
        "起草人": _split_orgs(first(r"起草人[：:]\s*([^\n]+)")),
        "引用文件": references,
        "专业术语": terms,
        "标准性质": _infer_nature(standard_number),
        "制修订": "修订" if "代替" in text else "制订",
    }


def _section_items(text: str, labels: list[str], *, stop_prefixes: list[str]) -> list[str]:
    lines = [line.strip() for line in text.splitlines()]
    start = None
    for index, line in enumerate(lines):
        if any(label in line for label in labels):
            start = index + 1
            break
    if start is None:
        return []
    items: list[str] = []
    for line in lines[start : start + 40]:
        if not line:
            continue
        if any(line.startswith(prefix) for prefix in stop_prefixes) or line.startswith("#"):
            if items:
                break
        cleaned = line.strip("-* ；;")
        if cleaned:
            items.append(cleaned)
    return items[:30]


def _split_orgs(value: str) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in re.split(r"[、,，;；]", value) if item.strip()]


def _infer_level(text: str, standard_number: str) -> str:
    normalized = standard_number.upper().replace(" ", "")
    if normalized.startswith("GB") or "国家标准" in text:
        return "国家标准"
    if normalized.startswith("DB") or "地方标准" in text:
        return "地方标准"
    if standard_number or "行业标准" in text:
        return "行业标准"
    return ""


def _infer_nature(standard_number: str) -> str:
    normalized = standard_number.upper().replace(" ", "")
    if "/T" in normalized:
        return "推荐性"
    if normalized.startswith("GB"):
        return "强制性"
    return ""

