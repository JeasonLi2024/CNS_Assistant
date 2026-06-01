from __future__ import annotations

import re
from typing import Any

from .doc_parser import ParsedMarkdownDocument
from .rule_models import AuditIssue


def run_format_source_audit(document: ParsedMarkdownDocument) -> tuple[list[AuditIssue], dict[str, Any]]:
    """对源文件（Word OOXML 或 PDF 版式）执行「格式规范」确定性审核。"""
    source_type = str(getattr(document, "source_type", "markdown") or "").lower()
    if source_type not in {"docx", "pdf"}:
        return [], {"enabled": False, "reason": f"unsupported_format_source:{source_type}"}

    facts = list(getattr(document, "format_facts", []) or [])
    issues: list[AuditIssue] = []
    evaluations: list[dict[str, Any]] = []

    # 仅「格式规范」章节对应检查（章/条/段/列项/目次页码）；目次结构等由 MD 内容轨审核。
    checks = [
        _check_chapter_numbering,
        _check_clause_depth_and_title_consistency,
        _check_dangling_paragraphs,
        _check_list_item_markers,
        _check_toc_page_consistency,
    ]
    for check in checks:
        produced, evaluation = check(document, facts)
        evaluations.append(evaluation)
        issues.extend(produced)

    trace = {
        "enabled": True,
        "source_type": source_type,
        "facts_total": len(facts),
        "facts_by_kind": _facts_by_kind(facts),
        "evaluations": evaluations,
        "issue_count": len([x for x in issues if x.status == "fail"]),
    }
    return issues, trace


def run_word_format_audit(document: ParsedMarkdownDocument) -> tuple[list[AuditIssue], dict[str, Any]]:
    """兼容旧调用名。"""
    return run_format_source_audit(document)


def summarize_format_facts(document: ParsedMarkdownDocument) -> dict[str, Any]:
    facts = list(getattr(document, "format_facts", []) or [])
    return {
        "source_type": getattr(document, "source_type", "markdown"),
        "text_view_chars": len(getattr(document, "text_view", "") or document.raw_text or ""),
        "facts_total": len(facts),
        "facts_by_kind": _facts_by_kind(facts),
        "locations_total": len(getattr(document, "source_locations", {}) or {}),
    }


def _format_reasoning_note(document: ParsedMarkdownDocument) -> str:
    source_type = str(getattr(document, "source_type", "markdown") or "").lower()
    if source_type == "pdf":
        return "基于 PDF 版式解析（页脚页码、目次页文本、章条标题行）的格式规范确定性判断。"
    if source_type == "docx":
        return "基于 Word OOXML/样式抽取出的结构化格式事实进行格式规范确定性判断。"
    return "基于源文件结构化格式事实进行确定性判断。"


def _facts_by_kind(facts: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for fact in facts:
        kind = str(fact.get("kind") or "unknown")
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def _check_toc_page_consistency(document: ParsedMarkdownDocument, facts: list[dict[str, Any]]) -> tuple[list[AuditIssue], dict[str, Any]]:
    rule_id = "DOCX-TOC-001"
    expected = "Word 目次中标注的页码应与正文中对应标题的页码一致。"
    alignment = _build_toc_alignment(document, facts)
    if alignment.get("skipped"):
        return [], {
            "rule_id": rule_id,
            "rule_name": "Word 目次页码一致性",
            "checked": 0,
            "failed": 0,
            "skipped": True,
            "skip_reason": alignment.get("skip_reason"),
        }
    failed: list[dict[str, Any]] = []
    skipped_unreliable = 0
    if not alignment["toc_entries"]:
        issue = _single_info_issue(
        document,
        rule_id=rule_id,
        rule_name="Word 目次页码一致性",
        scope="toc",
        status="insufficient_context",
            expected=expected,
            actual="未能从 Word 目次中抽取出带页码的条目；可能是 TOC 域尚未更新或未保留结果文本。",
        evidence_text="toc_entries=0",
        suggestion="在 Word 中更新目录域并保存，或提供保留 TOC 结果文本的 .docx。",
        source_ref="rules_test.md#格式规范-p6",
        )
        return [issue], {"rule_id": rule_id, "rule_name": "Word 目次页码一致性", "checked": 0, "failed": 0, "not_ready": 1}
    skipped = len(alignment["toc_only"])
    page_matches = alignment.get("page_audit_matches") or []
    for match in page_matches:
        entry = match["toc"]
        heading = match["heading"]
        if entry.get("page") is None or heading.get("page") is None:
            skipped += 1
            continue
        if entry.get("page_scheme") and heading.get("page_scheme") and entry["page_scheme"] != heading["page_scheme"]:
            skipped += 1
            continue
        adjusted_actual = _adjust_heading_page_for_toc(entry, heading["page"], page_matches)
        if str(adjusted_actual) != str(entry["page"]):
            if heading.get("page_estimate_unreliable"):
                skipped_unreliable += 1
                continue
            failed.append({**entry, "_actual_page": adjusted_actual, "_raw_page_estimate": heading.get("page_physical", heading["page"])})
    issues = _issues_from_failed(
        document,
        rule_id=rule_id,
        rule_name="Word 目次页码一致性",
        scope="toc",
        expected=expected,
        failed=failed,
        actual_builder=lambda f: f"目次页码={f.get('page')}，正文估算页码={f.get('_actual_page')}（原始版面估算={f.get('_raw_page_estimate')}）；标题={f.get('title')}；位置={f.get('location_id')}",
        suggestion="在 Word 中更新目录域，并确认对应标题样式/书签没有断裂。",
        source_ref="rules_test.md#格式规范-p6",
    )
    evaluation = {
        "rule_id": rule_id,
        "rule_name": "Word 目次页码一致性",
        "checked": len(page_matches),
        "matched": len(page_matches),
        "failed": len(failed),
        "skipped_no_body_heading": skipped,
        "skipped_unreliable_page_estimate": skipped_unreliable,
        "skipped_self_toc_or_out_of_scope": len(alignment["matches"]) - len(page_matches),
    }
    return issues, evaluation


def _check_chapter_numbering(document: ParsedMarkdownDocument, facts: list[dict[str, Any]]) -> tuple[list[AuditIssue], dict[str, Any]]:
    rule_id = "DOCX-FMT-001"
    expected = "章编号应从“范围”一章开始，使用从 1 开始的连续阿拉伯数字，章标题置于编号之后。"
    chapters = [f for f in facts if f.get("kind") == "paragraph" and f.get("role") == "chapter"]
    failed: list[dict[str, Any]] = []
    expected_no = 1
    for fact in chapters:
        heading = str(fact.get("display_heading") or fact.get("text_excerpt") or "")
        m = re.match(r"^(\d+)\s+(.+)$", heading)
        if not m:
            failed.append({**fact, "_reason": "章标题缺少显式编号"})
            continue
        number = int(m.group(1))
        title = m.group(2).strip()
        if number != expected_no:
            failed.append({**fact, "_reason": f"章编号应为 {expected_no}，实际为 {number}"})
        if not title:
            failed.append({**fact, "_reason": "章编号之后缺少章标题"})
        expected_no = number + 1

    evaluation = {
        "rule_id": rule_id,
        "rule_name": "章编号连续性",
        "checked": len(chapters),
        "failed": len(failed),
    }
    return _issues_from_failed(
        document,
        rule_id=rule_id,
        rule_name="章编号连续性",
        scope="body",
        expected=expected,
        failed=failed[:20],
        actual_builder=lambda f: f"{f.get('_reason')}; {_location_text(f)}；标题={f.get('display_heading') or f.get('text_excerpt')}",
        suggestion="使用章标题样式或显式编号，确保从“1 范围”开始连续编号。",
        source_ref="rules_test.md#格式规范-p1",
    ), evaluation


def _check_clause_depth_and_title_consistency(document: ParsedMarkdownDocument, facts: list[dict[str, Any]]) -> tuple[list[AuditIssue], dict[str, Any]]:
    rule_id = "DOCX-FMT-002"
    expected = "条编号最多分到第五层次；同一层次的条有无标题应统一。术语条目编号不按条编号判定。"
    clauses = [f for f in facts if f.get("kind") == "paragraph" and f.get("role") == "clause" and f.get("scope") != "terms_definitions"]
    by_level: dict[int, list[bool]] = {}
    failed: list[dict[str, Any]] = []
    for fact in clauses:
        heading = str(fact.get("display_heading") or fact.get("text_excerpt") or "")
        m = re.match(r"^(\d+(?:\.\d+)+)\s*(.*)$", heading)
        if not m:
            continue
        level = m.group(1).count(".") + 1
        has_title = bool(m.group(2).strip())
        by_level.setdefault(level, []).append(has_title)
        if level > 5:
            failed.append({**fact, "_reason": f"条编号层次为 {level}，超过第五层次"})
    for level, title_flags in by_level.items():
        if any(title_flags) and not all(title_flags):
            for fact in clauses:
                heading = str(fact.get("display_heading") or fact.get("text_excerpt") or "")
                m = re.match(r"^(\d+(?:\.\d+)+)\s*(.*)$", heading)
                if m and m.group(1).count(".") + 1 == level:
                    failed.append({**fact, "_reason": f"第 {level} 层条标题有无不统一"})
    evaluation = {
        "rule_id": rule_id,
        "rule_name": "条编号层次与标题统一性",
        "checked": len(clauses),
        "failed": len(failed),
    }
    return _issues_from_failed(
        document,
        rule_id=rule_id,
        rule_name="条编号层次与标题统一性",
        scope="body",
        expected=expected,
        failed=failed[:20],
        actual_builder=lambda f: f"{f.get('_reason')}; {_location_text(f)}；标题={f.get('display_heading') or f.get('text_excerpt')}",
        suggestion="减少条编号层级，或在同一层级统一采用“有标题”或“无标题”的形式。",
        source_ref="rules_test.md#格式规范-p2",
    ), evaluation


def _check_dangling_paragraphs(document: ParsedMarkdownDocument, facts: list[dict[str, Any]]) -> tuple[list[AuditIssue], dict[str, Any]]:
    rule_id = "DOCX-FMT-003"
    expected = "不宜在章标题与条之间或条标题与下一层次条之间设置悬置段；术语和定义、符号和缩略语中的引导语除外。"
    paras = [f for f in facts if f.get("kind") == "paragraph"]
    failed: list[dict[str, Any]] = []
    for idx, fact in enumerate(paras[:-1]):
        role = str(fact.get("role") or "")
        if role not in {"chapter", "clause"}:
            continue
        scope = str(fact.get("scope") or "")
        if scope in {"terms_definitions", "symbols_abbreviations"}:
            continue
        nxt = paras[idx + 1]
        if str(nxt.get("role") or "") == "paragraph" and str(nxt.get("scope") or "") == scope:
            following_struct = next((x for x in paras[idx + 2 : idx + 8] if str(x.get("role") or "") in {"clause", "chapter"}), None)
            if following_struct is None or str(following_struct.get("role") or "") != "clause":
                continue
            if role == "chapter":
                failed.append({**nxt, "_reason": f"位于章标题“{fact.get('display_heading') or fact.get('text_excerpt')}”与第一条之间"})
                continue
            cur_level = _heading_level(str(fact.get("display_heading") or fact.get("text_excerpt") or ""))
            next_level = _heading_level(str(following_struct.get("display_heading") or following_struct.get("text_excerpt") or ""))
            if next_level > cur_level:
                failed.append({**nxt, "_reason": f"位于条标题“{fact.get('display_heading') or fact.get('text_excerpt')}”与下一层次条之间"})
    evaluation = {
        "rule_id": rule_id,
        "rule_name": "悬置段检查",
        "checked": len(paras),
        "failed": len(failed),
    }
    return _issues_from_failed(
        document,
        rule_id=rule_id,
        rule_name="悬置段检查",
        scope="body",
        expected=expected,
        failed=failed[:20],
        actual_builder=lambda f: f"{f.get('_reason')}; {_location_text(f)}；段落={f.get('text_excerpt')}",
        suggestion="将悬置段改写为条标题后的条文，或补充分条结构；术语/符号引导语可保留。",
        source_ref="rules_test.md#格式规范-p3",
    ), evaluation


def _check_list_item_markers(document: ParsedMarkdownDocument, facts: list[dict[str, Any]]) -> tuple[list[AuditIssue], dict[str, Any]]:
    rule_id = "DOCX-FMT-004"
    expected = "列项前应标明破折号、间隔号、小写拉丁字母编号或阿拉伯数字编号，且同一列项组格式应统一。"
    list_facts = [
        f for f in facts
        if f.get("kind") == "paragraph" and ("列项" in str(f.get("style_name") or "") or re.match(r"^(——|·|[a-z]）|\d+）)", str(f.get("text_excerpt") or "")))
    ]
    failed: list[dict[str, Any]] = []
    for fact in list_facts:
        text = str(fact.get("text_excerpt") or "").strip()
        if "列项" in str(fact.get("style_name") or ""):
            continue
        if not re.match(r"^(——|·|[a-z]）|\d+）)", text) and not str(fact.get("raw_ooxml_ref", {}).get("numbering_id") or ""):
            failed.append({**fact, "_reason": "列项样式存在，但文本和 OOXML 均未发现明确列项符号/编号"})
    evaluation = {
        "rule_id": rule_id,
        "rule_name": "列项符号检查",
        "checked": len(list_facts),
        "failed": len(failed),
    }
    return _issues_from_failed(
        document,
        rule_id=rule_id,
        rule_name="列项符号检查",
        scope="body",
        expected=expected,
        failed=failed[:20],
        actual_builder=lambda f: f"{f.get('_reason')}; {_location_text(f)}；段落={f.get('text_excerpt')}",
        suggestion="使用标准列项符号或编号，并避免只通过缩进模拟列项。",
        source_ref="rules_test.md#格式规范-p4",
    ), evaluation


def _issues_from_failed(
    document: ParsedMarkdownDocument,
    *,
    rule_id: str,
    rule_name: str,
    scope: str,
    expected: str,
    failed: list[dict[str, Any]],
    actual_builder,
    suggestion: str,
    severity: str = "中度",
    source_ref: str | None = None,
) -> list[AuditIssue]:
    out: list[AuditIssue] = []
    for index, fact in enumerate(failed, start=1):
        location_id = str(fact.get("location_id") or "")
        evidence = f"{location_id} | {fact.get('text_excerpt', '')}".strip()
        out.append(
            AuditIssue(
                issue_id=f"{rule_id}-{index:03d}",
                file_name=document.file_name,
                rule_id=rule_id,
                rule_name=rule_name,
                scope=scope,
                severity=severity,
                status="fail",
                expected=expected,
                actual=str(actual_builder(fact)),
                evidence_text=evidence[:1000],
                source_ref=source_ref or f"word_format::{location_id or rule_id}",
                suggestion=suggestion,
                confidence=1.0,
                llm_reasoning=_format_reasoning_note(document),
            )
        )
    return out


def _single_info_issue(
    document: ParsedMarkdownDocument,
    *,
    rule_id: str,
    rule_name: str,
    scope: str,
    status: str,
    expected: str,
    actual: str,
    evidence_text: str,
    suggestion: str,
    source_ref: str | None = None,
) -> AuditIssue:
    return AuditIssue(
        issue_id=f"{rule_id}-INFO",
        file_name=document.file_name,
        rule_id=rule_id,
        rule_name=rule_name,
        scope=scope,
        severity="轻度",
        status=status,
        expected=expected,
        actual=actual,
        evidence_text=evidence_text,
        source_ref=source_ref or f"word_format::{rule_id}",
        suggestion=suggestion,
        confidence=1.0,
        llm_reasoning=_format_reasoning_note(document),
    )


def _build_toc_alignment(document: ParsedMarkdownDocument, facts: list[dict[str, Any]]) -> dict[str, Any]:
    if not (document.toc_text or "").strip():
        return {"skipped": True, "skip_reason": "empty_toc_scope"}
    toc_entries = _extract_toc_entries(facts)
    body_headings = _extract_body_headings(facts)
    _apply_logical_page_numbers(toc_entries, body_headings, facts)
    used_heading_ids: set[str] = set()
    matches: list[dict[str, Any]] = []
    toc_only: list[dict[str, Any]] = []
    for entry in toc_entries:
        heading = _match_toc_entry(entry, body_headings, used_heading_ids)
        if heading is None:
            toc_only.append(entry)
            continue
        used_heading_ids.add(str(heading.get("location_id") or ""))
        bookmark_hit = bool(
            set(entry.get("bookmark_refs") or []).intersection(set(heading.get("bookmark_names") or []))
        )
        matches.append({"toc": entry, "heading": heading, "bookmark_match": bookmark_hit})
    _calibrate_logical_pages_from_matches(matches)
    page_audit_matches = [m for m in matches if _is_toc_page_audit_match(m)]
    body_only = [h for h in body_headings if str(h.get("location_id") or "") not in used_heading_ids]
    order_mismatch: list[dict[str, Any]] = []
    body_indexes = [int(m["heading"].get("paragraph_index") or 0) for m in matches]
    if body_indexes != sorted(body_indexes):
        for m in matches:
            order_mismatch.append(m["toc"])
    return {
        "skipped": False,
        "toc_entries": toc_entries,
        "body_headings": body_headings,
        "matches": matches,
        "page_audit_matches": page_audit_matches,
        "toc_only": toc_only,
        "body_only": body_only,
        "order_mismatch": order_mismatch,
    }


def _min_document_physical_page(facts: list[dict[str, Any]]) -> int:
    """文档罗马节起点的物理页（含封面/目次等），用于前言/引言逻辑页，不假定前言必为 I 或 II。"""
    values: list[int] = []
    for fact in facts:
        if fact.get("kind") != "paragraph":
            continue
        raw = fact.get("raw_ooxml_ref")
        if not isinstance(raw, dict):
            continue
        pe = _safe_int(raw.get("page_estimate"))
        if pe is not None:
            values.append(pe)
    return min(values) if values else 1


def _apply_logical_page_numbers(
    toc_entries: list[dict[str, Any]],
    body_headings: list[dict[str, Any]],
    facts: list[dict[str, Any]],
) -> None:
    physical_pages = [h.get("page") for h in body_headings if h.get("page") is not None]
    if not physical_pages:
        return

    for entry in toc_entries:
        entry["page_scheme"] = _toc_entry_page_scheme(
            str(entry.get("number") or ""),
            str(entry.get("title_text") or entry.get("title") or ""),
        )

    roman_anchor_physical = _min_document_physical_page(facts)
    body_physical = [
        int(h["page"])
        for h in body_headings
        if h.get("scope") not in {"foreword", "introduction", "toc"} and h.get("page") is not None
    ]
    min_body_physical = min(body_physical) if body_physical else None

    for heading in body_headings:
        physical = heading.get("page")
        if physical is None:
            continue
        heading["page_physical"] = int(physical)
        scope = str(heading.get("scope") or "")
        if scope in {"foreword", "introduction"}:
            # 罗马逻辑页 = 相对文档首页的物理页序（目次占页则前言可为 II，无目次则可为 I）。
            heading["page"] = int(physical) - int(roman_anchor_physical) + 1
            heading["page_scheme"] = "roman_front_matter"
        elif scope != "toc" and min_body_physical is not None:
            heading["page"] = int(physical) - int(min_body_physical) + 1
            heading["page_scheme"] = "arabic_body"
        elif scope != "toc":
            heading["page"] = int(physical)
            heading["page_scheme"] = "arabic_body"
        _assess_page_estimate_reliability(heading)


def _toc_entry_page_scheme(number: str, title_text: str) -> str:
    if (number or "").strip():
        return "arabic_body"
    title_key = _normalize_heading_title(title_text)
    if title_key.startswith("前言") or title_key.startswith("引言"):
        return "roman_front_matter"
    return "arabic_body"


def _assess_page_estimate_reliability(heading: dict[str, Any]) -> None:
    """表格可能影响分页估算；有书签锚点或可靠匹配校准后仍可用于页码比对。"""
    table_breaks = int(heading.get("table_page_breaks_before") or 0)
    has_bookmark = bool(heading.get("bookmark_names"))
    calibrated = bool(heading.get("page_calibrated"))
    heading["page_estimate_unreliable"] = table_breaks > 0 and not has_bookmark and not calibrated


def _is_toc_self_reference_entry(entry: dict[str, Any]) -> bool:
    for raw in (
        str(entry.get("title") or ""),
        str(entry.get("title_text") or ""),
        str(entry.get("text_excerpt") or ""),
        str(entry.get("display") or ""),
    ):
        compact = _normalize_heading_title(raw)
        if not compact:
            continue
        if compact in {"目次", "目录", "contents"}:
            return True
        if compact.startswith("目次") and len(compact) <= 3:
            return True
        if compact.startswith("目录") and len(compact) <= 3:
            return True
    return False


def _is_toc_page_audit_match(match: dict[str, Any]) -> bool:
    """页码比对仅覆盖前言及后续章节，排除「目次」自身行。"""
    entry = match.get("toc") or {}
    heading = match.get("heading") or {}
    if _is_toc_self_reference_entry(entry):
        return False
    if str(heading.get("scope") or "") == "toc":
        return False
    scheme = str(entry.get("page_scheme") or _toc_entry_page_scheme(
        str(entry.get("number") or ""),
        str(entry.get("title_text") or entry.get("title") or ""),
    ))
    if scheme == "roman_front_matter":
        return str(heading.get("scope") or "") in {"foreword", "introduction"}
    if str(entry.get("number") or "").strip():
        return True
    title_key = _normalize_heading_title(str(entry.get("title_text") or entry.get("title") or ""))
    if re.match(r"^\d+(?:\.\d+)*", title_key):
        return True
    if title_key.startswith("附录"):
        return str(heading.get("role") or "") == "appendix_title"
    return False


def _calibrate_logical_pages_from_matches(matches: list[dict[str, Any]]) -> None:
    """用书签可靠匹配的对齐项校准同 scheme 下其余标题的逻辑页，缓解表格导致的估算漂移。"""
    for scheme in ("roman_front_matter", "arabic_body"):
        deltas: list[int] = []
        for match in matches:
            toc = match.get("toc") or {}
            heading = match.get("heading") or {}
            if toc.get("page_scheme") != scheme or heading.get("page_scheme") != scheme:
                continue
            if toc.get("page") is None or heading.get("page") is None:
                continue
            if not match.get("bookmark_match") and heading.get("page_estimate_unreliable"):
                continue
            try:
                deltas.append(int(toc["page"]) - int(heading["page"]))
            except (TypeError, ValueError):
                continue
        if not deltas or not all(x == deltas[0] for x in deltas):
            continue
        offset = deltas[0]
        if offset == 0:
            for match in matches:
                h = match.get("heading") or {}
                if h.get("page_scheme") == scheme:
                    h["page_calibrated"] = True
                    h["page_estimate_unreliable"] = False
            continue
        for match in matches:
            heading = match.get("heading") or {}
            if heading.get("page_scheme") != scheme or heading.get("page") is None:
                continue
            heading["page"] = int(heading["page"]) + offset
            heading["page_calibrated"] = True
            heading["page_estimate_unreliable"] = False


def _extract_toc_entries(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for fact in facts:
        if fact.get("kind") != "paragraph":
            continue
        if fact.get("scope") != "toc" and str(fact.get("role") or "") != "toc_entry":
            continue
        text = str(fact.get("text_excerpt") or "").strip()
        if not text:
            continue
        title, page = _split_toc_title_page(text)
        if not title:
            continue
        number, title_text = _split_number_title(title)
        if _is_toc_self_reference_entry(
            {"title": title, "title_text": title_text, "text_excerpt": text, "display": title}
        ):
            continue
        raw = fact.get("raw_ooxml_ref") if isinstance(fact.get("raw_ooxml_ref"), dict) else {}
        page_scheme = _toc_entry_page_scheme(number, title_text)
        entries.append({
            "location_id": fact.get("location_id"),
            "paragraph_index": fact.get("paragraph_index"),
            "number": number,
            "title": title,
            "title_text": title_text,
            "page": page,
            "page_scheme": page_scheme,
            "text_excerpt": text,
            "key": _heading_key_parts(number, title_text),
            "title_key": _normalize_heading_title(title_text),
            "bookmark_refs": _field_bookmark_refs(str(raw.get("field_instr") or "")),
            "display": title,
        })
    return entries


def _extract_body_headings(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for fact in facts:
        if fact.get("kind") != "paragraph":
            continue
        role = str(fact.get("role") or "")
        scope = str(fact.get("scope") or "")
        if role not in {"front_title", "chapter", "clause", "appendix_title"}:
            continue
        if scope == "toc":
            continue
        if scope == "terms_definitions" and role == "clause":
            continue
        heading = str(fact.get("display_heading") or fact.get("text_excerpt") or "")
        number, title_text = _split_number_title(heading)
        key = _heading_key_parts(number, title_text)
        if not key and not title_text:
            continue
        raw = fact.get("raw_ooxml_ref") if isinstance(fact.get("raw_ooxml_ref"), dict) else {}
        out.append({
            "location_id": fact.get("location_id"),
            "paragraph_index": fact.get("paragraph_index"),
            "scope": scope,
            "role": role,
            "number": number,
            "title": heading,
            "title_text": title_text,
            "page": _safe_int(raw.get("page_estimate")),
            "text_excerpt": fact.get("text_excerpt"),
            "key": key,
            "title_key": _normalize_heading_title(title_text),
            "bookmark_names": list(raw.get("bookmark_names") or []),
            "table_page_breaks_before": _safe_int(raw.get("table_page_breaks_before")) or 0,
            "body_tables_before": _safe_int(raw.get("body_tables_before")) or 0,
            "page_calibrated": False,
            "page_estimate_unreliable": False,
            "display": heading,
        })
    return out


def _match_toc_entry(entry: dict[str, Any], headings: list[dict[str, Any]], used_ids: set[str]) -> dict[str, Any] | None:
    bookmark_refs = set(entry.get("bookmark_refs") or [])
    if bookmark_refs:
        for heading in headings:
            if str(heading.get("location_id") or "") in used_ids:
                continue
            if bookmark_refs.intersection(set(heading.get("bookmark_names") or [])):
                return heading
    key = str(entry.get("key") or "")
    if key:
        for heading in headings:
            if str(heading.get("location_id") or "") not in used_ids and heading.get("key") == key:
                return heading
    title_key = str(entry.get("title_key") or "")
    for heading in headings:
        if str(heading.get("location_id") or "") in used_ids:
            continue
        if title_key and title_key == heading.get("title_key"):
            return heading
    return None


def _adjust_heading_page_for_toc(entry: dict[str, Any], actual: int, matches: list[dict[str, Any]]) -> int:
    scheme = str(entry.get("page_scheme") or "")
    candidates: list[int] = []
    for match in matches:
        toc = match["toc"]
        heading = match["heading"]
        if scheme and toc.get("page_scheme") != scheme:
            continue
        if heading.get("page_calibrated"):
            continue
        body_page = heading.get("page")
        toc_page = toc.get("page")
        if body_page is None or toc_page is None:
            continue
        try:
            candidates.append(int(body_page) - int(toc_page))
        except (TypeError, ValueError):
            continue
    offset = candidates[0] if candidates and all(x == candidates[0] for x in candidates) else 0
    return actual - offset


def _split_toc_title_page(text: str) -> tuple[str, int | None]:
    raw = re.sub(r"\s+", " ", text or "").strip()
    m = re.match(r"^(?P<title>.+?)\s*(?:\.{2,}|\t|\s)(?P<page>[IVXLCM]+|\d+)\s*$", raw, flags=re.IGNORECASE)
    if not m:
        m = re.match(r"^(?P<title>.+?)(?P<page>[IVXLCM]+|\d+)\s*$", raw, flags=re.IGNORECASE)
    if not m:
        return raw, None
    return m.group("title").strip(), _roman_or_int(m.group("page"))


def _split_number_title(value: str) -> tuple[str, str]:
    text = re.sub(r"\s+", " ", value or "").strip()
    m = re.match(r"^(?P<number>\d+(?:\.\d+)*)\.?\s+(?P<title>.+)$", text)
    if not m:
        m = re.match(r"^(?P<number>\d+(?:\.\d+)+)(?P<title>\D.+)$", text)
    if m:
        number = re.sub(r"\.$", "", m.group("number"))
        return number, m.group("title").strip()
    return "", text


def _heading_key_parts(number: str, title: str) -> str:
    title_key = _normalize_heading_title(title)
    number = re.sub(r"\s+", "", number or "")
    if number:
        return f"{number}|{title_key}"
    return title_key


def _normalize_heading_title(value: str) -> str:
    text = re.sub(r"^#+\s*", "", value or "")
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[（）()【】《》\"'：:，,。；;\-—/、]", "", text)
    return text


def _field_bookmark_refs(field_instr: str) -> list[str]:
    refs: list[str] = []
    for match in re.finditer(r"\bPAGEREF\s+([^\s\\]+)", field_instr or "", flags=re.IGNORECASE):
        refs.append(match.group(1))
    return refs


def _entry_display(entry: dict[str, Any]) -> str:
    return str(entry.get("display") or entry.get("title") or entry.get("text_excerpt") or "").strip()


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_rule_text(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[：:，,。；;（）()\[\]【】《》\-—/、\u201c\u201d\u2018\u2019]", "", text)
    return text


def _heading_level(heading: str) -> int:
    m = re.match(r"^\s*(\d+(?:\.\d+)*)", heading or "")
    if not m:
        return 0
    return m.group(1).count(".") + 1


def _heading_key(value: str) -> str:
    text = re.sub(r"\s+", "", value or "")
    text = re.sub(r"(?:\.{2,})?(?:[IVXLCM]+|\d+)$", "", text, flags=re.IGNORECASE)
    return text.strip()


def _roman_or_int(value: str) -> int | None:
    raw = (value or "").strip()
    if raw.isdigit():
        return int(raw)
    roman = raw.upper()
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    prev = 0
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


def _is_heading_fact(fact: dict[str, Any]) -> bool:
    text = str(fact.get("text_excerpt") or "")
    style = str(fact.get("style_name") or "")
    if "标题" in style or "Heading" in style:
        return True
    return bool(text in {"前言", "引言", "目次", "目录", "参考文献", "索引"} or re.match(r"^\d+(?:\.\d+)*\s+", text))


def _paragraph_value(fact: dict[str, Any], key: str) -> Any:
    paragraph = fact.get("paragraph")
    if isinstance(paragraph, dict):
        return paragraph.get(key)
    return None


def _location_text(fact: dict[str, Any]) -> str:
    loc = str(fact.get("location_id") or "")
    style = str(fact.get("style_name") or "")
    return f"位置={loc}，样式={style or '未命名样式'}"


def _section_actual(fact: dict[str, Any]) -> str:
    section = fact.get("section") if isinstance(fact.get("section"), dict) else {}
    return (
        f"page_width={section.get('page_width_cm')}cm, "
        f"page_height={section.get('page_height_cm')}cm, "
        f"orientation={section.get('orientation')}；{_location_text(fact)}"
    )


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
