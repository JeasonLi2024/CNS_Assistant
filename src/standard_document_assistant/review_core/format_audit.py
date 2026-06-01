"""Deterministic source-format audit for standard documents."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from standard_document_assistant.schemas import ReviewIssue


def run_format_source_audit(source_path: Path, source_virtual_path: str) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    """Run deterministic format checks against the original source file."""

    suffix = source_path.suffix.lower()
    warnings: list[str] = []
    if suffix == ".docx":
        facts = _parse_docx_facts(source_path)
        issues = _audit_facts(facts, source_virtual_path)
        return issues, _trace("docx", facts, issues), warnings
    if suffix == ".pdf":
        try:
            facts = _parse_pdf_facts(source_path)
        except RuntimeError as exc:
            issue = _insufficient_context_issue(
                source_virtual_path=source_virtual_path,
                actual=str(exc),
                suggestion="安装 pymupdf 后重新运行，或提供 DOCX 源文件以执行格式轨审核。",
            )
            warnings.append(str(exc))
            return [issue.model_dump()], _trace("pdf", [], [issue.model_dump()]), warnings
        issues = _audit_facts(facts, source_virtual_path)
        return issues, _trace("pdf", facts, issues), warnings
    return [], {"enabled": False, "reason": f"unsupported_format_source:{suffix}"}, [
        f"不支持的格式轨源文件：{suffix}"
    ]


def _parse_docx_facts(source_path: Path) -> list[dict[str, Any]]:
    try:
        from docx import Document
    except ImportError as exc:  # pragma: no cover - depends on optional dependency
        raise RuntimeError("缺少 python-docx，无法执行 DOCX 格式轨审核。") from exc

    document = Document(str(source_path))
    facts: list[dict[str, Any]] = []
    current_scope = "front_matter"
    for index, paragraph in enumerate(document.paragraphs, start=1):
        text = _clean_text(paragraph.text)
        if not text:
            continue
        style_name = str(getattr(paragraph.style, "name", "") or "")
        role = _classify_docx_paragraph(text, style_name)
        display_heading = text if role in {"chapter", "clause", "front_title", "toc_title"} else ""
        if role == "chapter":
            current_scope = _scope_for_heading(text)
        elif role == "front_title":
            current_scope = _front_scope(text)
        elif role == "toc_title":
            current_scope = "toc"
        elif current_scope == "toc" and _looks_like_toc_entry(text, style_name):
            role = "toc_entry"
        facts.append(
            {
                "location_id": f"p:{index}",
                "kind": "paragraph",
                "scope": current_scope,
                "text_excerpt": text[:300],
                "style_name": style_name,
                "role": role,
                "display_heading": display_heading,
                "paragraph_index": index,
            }
        )
    return facts


def _parse_pdf_facts(source_path: Path) -> list[dict[str, Any]]:
    try:
        import fitz  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - depends on optional dependency
        raise RuntimeError("缺少 pymupdf，无法执行 PDF 格式轨审核。") from exc

    doc = fitz.open(str(source_path))
    facts: list[dict[str, Any]] = []
    try:
        if doc.page_count == 0:
            raise RuntimeError("PDF 没有可审核页面。")
        para_index = 0
        for page_index in range(doc.page_count):
            text = doc[page_index].get_text("text") or ""
            for line in text.splitlines():
                value = _clean_text(line)
                if not value:
                    continue
                role = _classify_pdf_line(value)
                if role == "paragraph" and not _looks_like_list_item(value):
                    continue
                para_index += 1
                facts.append(
                    {
                        "location_id": f"pdf:p{page_index + 1}:l{para_index}",
                        "kind": "paragraph",
                        "scope": _scope_for_heading(value) if role == "chapter" else "other_body",
                        "text_excerpt": value[:300],
                        "style_name": "pdf-line",
                        "role": role,
                        "display_heading": value if role in {"chapter", "clause"} else "",
                        "paragraph_index": para_index,
                        "pdf_page": page_index + 1,
                    }
                )
    finally:
        doc.close()
    if not facts:
        raise RuntimeError("PDF 未抽取到可复制文字层或可识别结构行。")
    return facts


def _audit_facts(facts: list[dict[str, Any]], source_virtual_path: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    checks = [
        _check_chapter_numbering,
        _check_clause_depth_and_title_consistency,
        _check_dangling_paragraphs,
        _check_list_item_markers,
    ]
    for check in checks:
        issues.extend(check(facts, source_virtual_path))
    if not facts:
        issues.append(
            _insufficient_context_issue(
                source_virtual_path=source_virtual_path,
                actual="未抽取到格式事实。",
                suggestion="提供带可识别文字和标题结构的 DOCX/PDF 源文件。",
            ).model_dump()
        )
    return issues


def _check_chapter_numbering(facts: list[dict[str, Any]], source_virtual_path: str) -> list[dict[str, Any]]:
    chapters = [fact for fact in facts if fact.get("role") == "chapter"]
    failed: list[tuple[dict[str, Any], str]] = []
    expected_no = 1
    for fact in chapters:
        heading = str(fact.get("display_heading") or fact.get("text_excerpt") or "")
        match = re.match(r"^(\d+)\s+(.+)$", heading)
        if not match:
            failed.append((fact, "章标题缺少显式编号"))
            continue
        number = int(match.group(1))
        if number != expected_no:
            failed.append((fact, f"章编号应为 {expected_no}，实际为 {number}"))
        expected_no = number + 1
    return _issues_from_failed(
        failed,
        source_virtual_path=source_virtual_path,
        rule_id="FMT-CHAPTER-001",
        rule_name="章编号连续性",
        expected="章编号应从“1 范围”开始，使用连续阿拉伯数字。",
        suggestion="调整章编号，确保从“1 范围”开始连续编号。",
    )


def _check_clause_depth_and_title_consistency(facts: list[dict[str, Any]], source_virtual_path: str) -> list[dict[str, Any]]:
    clauses = [fact for fact in facts if fact.get("role") == "clause" and fact.get("scope") != "terms_definitions"]
    failed: list[tuple[dict[str, Any], str]] = []
    by_level: dict[int, list[bool]] = {}
    clause_level: dict[str, int] = {}
    for fact in clauses:
        heading = str(fact.get("display_heading") or fact.get("text_excerpt") or "")
        match = re.match(r"^(\d+(?:\.\d+)+)\s*(.*)$", heading)
        if not match:
            continue
        level = match.group(1).count(".") + 1
        clause_level[str(fact.get("location_id"))] = level
        has_title = bool(match.group(2).strip())
        by_level.setdefault(level, []).append(has_title)
        if level > 5:
            failed.append((fact, f"条编号层次为 {level}，超过第五层次"))
    for level, flags in by_level.items():
        if any(flags) and not all(flags):
            for fact in clauses:
                if clause_level.get(str(fact.get("location_id"))) == level:
                    failed.append((fact, f"第 {level} 层条标题有无不统一"))
    return _issues_from_failed(
        failed,
        source_virtual_path=source_virtual_path,
        rule_id="FMT-CLAUSE-001",
        rule_name="条编号层次与标题统一性",
        expected="条编号最多分到第五层次；同一层次的条有无标题应统一。",
        suggestion="减少条编号层级，或在同一层级统一采用有标题或无标题形式。",
    )


def _check_dangling_paragraphs(facts: list[dict[str, Any]], source_virtual_path: str) -> list[dict[str, Any]]:
    paragraphs = [fact for fact in facts if fact.get("kind") == "paragraph"]
    failed: list[tuple[dict[str, Any], str]] = []
    for index, fact in enumerate(paragraphs[:-1]):
        role = str(fact.get("role") or "")
        if role not in {"chapter", "clause"}:
            continue
        scope = str(fact.get("scope") or "")
        if scope in {"terms_definitions", "symbols_abbreviations", "toc"}:
            continue
        next_fact = paragraphs[index + 1]
        if next_fact.get("role") != "paragraph" or next_fact.get("scope") != scope:
            continue
        following = next(
            (
                item
                for item in paragraphs[index + 2 : index + 8]
                if item.get("role") in {"chapter", "clause"}
            ),
            None,
        )
        if not following or following.get("role") != "clause":
            continue
        if role == "chapter":
            failed.append((next_fact, f"位于章标题“{fact.get('display_heading')}”与第一条之间"))
            continue
        current_level = _heading_level(str(fact.get("display_heading") or fact.get("text_excerpt") or ""))
        next_level = _heading_level(str(following.get("display_heading") or following.get("text_excerpt") or ""))
        if next_level > current_level:
            failed.append((next_fact, f"位于条标题“{fact.get('display_heading')}”与下一层次条之间"))
    return _issues_from_failed(
        failed,
        source_virtual_path=source_virtual_path,
        rule_id="FMT-DANGLING-001",
        rule_name="悬置段检查",
        expected="不宜在章标题与条之间或条标题与下一层次条之间设置悬置段。",
        suggestion="将悬置段改写为条标题后的条文，或补充分条结构。",
    )


def _check_list_item_markers(facts: list[dict[str, Any]], source_virtual_path: str) -> list[dict[str, Any]]:
    list_like = [
        fact
        for fact in facts
        if "列项" in str(fact.get("style_name") or "") or _looks_like_list_item(str(fact.get("text_excerpt") or ""))
    ]
    failed: list[tuple[dict[str, Any], str]] = []
    for fact in list_like:
        text = str(fact.get("text_excerpt") or "").strip()
        if not _looks_like_list_item(text):
            failed.append((fact, "列项样式存在，但文本未发现明确列项符号或编号"))
    return _issues_from_failed(
        failed,
        source_virtual_path=source_virtual_path,
        rule_id="FMT-LIST-001",
        rule_name="列项符号检查",
        expected="列项前应标明破折号、间隔号、小写拉丁字母编号或阿拉伯数字编号。",
        suggestion="使用标准列项符号或编号，避免只通过缩进模拟列项。",
    )


def _issues_from_failed(
    failed: list[tuple[dict[str, Any], str]],
    *,
    source_virtual_path: str,
    rule_id: str,
    rule_name: str,
    expected: str,
    suggestion: str,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for index, (fact, reason) in enumerate(failed[:20], start=1):
        location = str(fact.get("location_id") or "")
        text = str(fact.get("text_excerpt") or "")
        issues.append(
            ReviewIssue(
                issue_id=f"{rule_id}-{index:03d}",
                rule_id=rule_id,
                rule_name=rule_name,
                scope=str(fact.get("scope") or "body"),
                audit_track="format_source",
                severity="major",
                status="fail",
                expected=expected,
                actual=f"{reason}；位置={location}；样式={fact.get('style_name') or '未知样式'}",
                evidence_text=f"{source_virtual_path}#{location} | {text}"[:1000],
                source_ref=f"format_source::{rule_id}",
                suggestion=suggestion,
                confidence=1.0,
                llm_reasoning="基于原始源文件解析出的格式事实进行确定性判断，未调用 LLM。",
            ).model_dump()
        )
    return issues


def _insufficient_context_issue(
    *,
    source_virtual_path: str,
    actual: str,
    suggestion: str,
) -> ReviewIssue:
    return ReviewIssue(
        issue_id="FMT-SOURCE-INFO",
        rule_id="FMT-SOURCE-000",
        rule_name="格式轨审核依据不足",
        scope="full_document",
        audit_track="format_source",
        severity="info",
        status="insufficient_context",
        expected="格式轨应基于原始 DOCX/PDF 的结构化格式事实执行确定性检查。",
        actual=actual,
        evidence_text=source_virtual_path,
        source_ref="format_source::availability",
        suggestion=suggestion,
        confidence=0.0,
        llm_reasoning="未获得足够格式事实，未调用 LLM。",
    )


def _trace(source_type: str, facts: list[dict[str, Any]], issues: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for fact in facts:
        role = str(fact.get("role") or "unknown")
        counts[role] = counts.get(role, 0) + 1
    return {
        "enabled": True,
        "source_type": source_type,
        "facts_total": len(facts),
        "facts_by_role": counts,
        "issue_count": len([item for item in issues if item.get("status") == "fail"]),
        "checks": [
            "chapter_numbering",
            "clause_depth_and_title_consistency",
            "dangling_paragraphs",
            "list_item_markers",
        ],
    }


def _classify_docx_paragraph(text: str, style_name: str) -> str:
    compact = re.sub(r"\s+", "", text)
    style = re.sub(r"\s+", "", style_name).lower()
    if compact in {"目次", "目录"}:
        return "toc_title"
    if compact in {"前言", "引言", "参考文献", "索引"}:
        return "front_title"
    if style.startswith("toc") or "目录" in style:
        return "toc_entry"
    if "章标题" in style or re.match(r"^[1-9]\d?\s+(?![-—–])\S", text):
        return "chapter"
    if "条标题" in style or re.match(r"^[1-9]\d?(?:\.\d+){1,4}(?:\s+\S|\s*$)", text):
        return "clause"
    return "paragraph"


def _classify_pdf_line(text: str) -> str:
    if re.match(r"^[1-9]\d?\s+(?![-—–])\S", text):
        return "chapter"
    if re.match(r"^[1-9]\d?(?:\.\d+){1,4}\s+\S", text):
        return "clause"
    return "paragraph"


def _scope_for_heading(text: str) -> str:
    compact = re.sub(r"[\s\u3000]+", "", text)
    if "范围" in compact:
        return "scope"
    if "规范性引用文件" in compact:
        return "normative_references"
    if "术语和定义" in compact:
        return "terms_definitions"
    if "缩略语" in compact or "符号" in compact:
        return "symbols_abbreviations"
    return "other_body"


def _front_scope(text: str) -> str:
    compact = re.sub(r"[\s\u3000]+", "", text)
    if compact.startswith("前言"):
        return "foreword"
    if compact.startswith("引言"):
        return "introduction"
    return "front_matter"


def _looks_like_toc_entry(text: str, style_name: str) -> bool:
    style = re.sub(r"\s+", "", style_name).lower()
    return style.startswith("toc") or "目录" in style or bool(
        re.search(r"(?:\.{2,}|\s)(?:\d+|[IVXLCM]+)\s*$", text, flags=re.I)
    )


def _looks_like_list_item(text: str) -> bool:
    return bool(re.match(r"^(——|—|·|[a-z]\)|[a-z]）|\d+\)|\d+）)", text.strip()))


def _heading_level(heading: str) -> int:
    match = re.match(r"^\s*(\d+(?:\.\d+)*)", heading or "")
    if not match:
        return 0
    return match.group(1).count(".") + 1


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").replace("\u3000", " ")).strip()

