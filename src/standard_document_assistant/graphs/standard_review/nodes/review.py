"""Content review subgraph: LLM Judge with multi-strategy + quality gate + widen loop."""

from __future__ import annotations

import asyncio
import json
import re
from collections import Counter
from typing import Any, Literal

from langgraph.types import Command

from standard_document_assistant.config import load_config
from standard_document_assistant.graphs.standard_review.events import emit_event
from standard_document_assistant.graphs.standard_review.state import StandardReviewState
from standard_document_assistant.pathing import utc_now_iso
from standard_document_assistant.review_core.llm_judge import (
    JudgePlan,
    LLMSoftRuleJudge,
    _JUDGE_SYSTEM_PROMPT,
)
from standard_document_assistant.review_core.rule_models import AuditIssue, RuleItem
from standard_document_assistant.review_core.serialization import deserialize_document


def judge_rules(state: StandardReviewState, runtime: Any = None) -> dict[str, Any]:
    if state.get("format_only"):
        result = {
            "trace_events": [emit_event(state, "judge_rules", "skipped")],
        }
        return _merge_coverage_check(state, result)

    config = load_config().standard_review
    if not config.enable_llm_review:
        return _merge_coverage_check(state, _deterministic_judge(state, config=config))

    try:
        judge = LLMSoftRuleJudge(config)
    except Exception as exc:
        result = {
            "errors": [f"LLM Judge 初始化失败：{exc}"],
            "trace_events": [emit_event(state, "judge_rules", "failed")],
        }
        return _merge_coverage_check(state, result)

    parsed_payload = state.get("parsed_document") or {}
    document = deserialize_document(parsed_payload)
    scope_text_map = dict(state.get("scope_text_map") or {})
    partial_mode = state.get("partial_mode") or "sectional"
    active_keys = list(state.get("active_scope_keys") or [])

    section_rules = [RuleItem.from_dict(item) for item in (state.get("section_rule_objects") or [])]
    full_rules = [RuleItem.from_dict(item) for item in (state.get("full_document_rule_objects") or [])]

    plans: list[JudgePlan] = []
    if partial_mode == "full_document" and full_rules:
        plans.extend(judge.plan(full_rules, document, scope_text_map))
    plans.extend(judge.plan(section_rules, document, scope_text_map))

    if not plans:
        result = {
            "warnings": ["未找到可执行的 LLM 审核计划，跳过内容轨。"],
            "trace_events": [emit_event(state, "judge_rules", "empty")],
        }
        return _merge_coverage_check(state, result)

    file_name = parsed_payload.get("file_name") or state.get("content_path") or "standard"
    trace_id = state.get("trace_id") or state.get("job_id") or ""
    try:
        outcomes = judge.run_dual_route(plans, file_name=file_name, trace_id=trace_id)
    except Exception as exc:
        result = {
            "errors": [f"LLM 审核并发失败：{exc}"],
            "trace_events": [emit_event(state, "judge_rules", "failed")],
        }
        return _merge_coverage_check(state, result)

    issues: list[dict[str, Any]] = []
    strategy_counter: Counter = Counter()
    insufficient_scopes: list[str] = []
    for outcome in outcomes:
        strategy_counter[outcome.strategy] += 1
        if outcome.issue is not None:
            issues.append(_issue_to_state_payload(outcome.issue))
            if outcome.issue.status == "insufficient_context" and outcome.issue.scope:
                if outcome.issue.scope not in insufficient_scopes:
                    insufficient_scopes.append(outcome.issue.scope)

    new_round = int(state.get("review_round") or 0) + 1
    result = {
        "issues": issues,
        "events": [
            {
                "trace_id": state.get("trace_id", ""),
                "node": "judge_rules",
                "event": "judge_summary",
                "strategies": dict(strategy_counter),
                "issues": len(issues),
                "round": new_round,
                "created_at": utc_now_iso(),
            }
        ],
        "insufficient_scopes": insufficient_scopes,
        "review_round": new_round,
        "trace_events": [
            emit_event(
                state,
                "judge_rules",
                "success",
                {
                    "plans": len(plans),
                    "issues": len(issues),
                    "strategies": dict(strategy_counter),
                    "round": new_round,
                },
            )
        ],
    }
    return _merge_coverage_check(state, result)


def quality_gate(
    state: StandardReviewState,
    runtime: Any = None,
) -> Command[Literal["widen_review_scope", "format_review"]]:
    """Decide whether to widen the review scope and rerun the judge.

    Routing logic:
    * if we haven't reached ``max_review_rounds`` and we collected at least
      one ``insufficient_context`` issue whose scope can be expanded to a
      ``full_document`` pass, go to ``widen_review_scope``;
    * otherwise jump to ``format_review`` for the deterministic format
      track; the format node itself decides whether to skip (no source
      file) or run, and is reachable from both full and format-only flows.
    """

    config = load_config().standard_review
    round_idx = int(state.get("review_round") or 0)
    max_rounds = int(state.get("max_review_rounds") or config.max_review_rounds)
    insufficient = list(state.get("insufficient_scopes") or [])
    partial_mode = state.get("partial_mode") or "sectional"

    if (
        not state.get("format_only")
        and config.enable_llm_review
        and partial_mode != "full_document"
        and insufficient
        and round_idx < max_rounds
    ):
        return Command(
            update={"trace_events": [emit_event(state, "quality_gate", "widen")]},
            goto="widen_review_scope",
        )
    return Command(
        update={"trace_events": [emit_event(state, "quality_gate", "ok", {"next": "format_review"})]},
        goto="format_review",
    )


def widen_review_scope(state: StandardReviewState) -> dict[str, Any]:
    """Promote the partial document view to full_document and request reload."""

    warnings: list[str] = []
    insufficient = list(state.get("insufficient_scopes") or [])
    round_idx = int(state.get("review_round") or 0) + 1
    for scope in insufficient:
        warnings.append(f"扩大审核范围：{scope}")
    return {
        "partial_mode": "full_document",
        "active_scope_keys": [
            "cover",
            "toc",
            "foreword",
            "scope",
            "normative_references",
            "terms_definitions",
            "other_body",
            "appendix",
            "end",
        ],
        "scope_text_map": state.get("scope_text_map") or {},
        "insufficient_scopes": [],
        "widened": True,
        "review_round": round_idx,
        "warnings": warnings,
        "trace_events": [emit_event(state, "widen_review_scope", "success", {"round": round_idx})],
    }


def reload_review_rules(state: StandardReviewState) -> dict[str, Any]:
    """Re-rank rules under the widened view. Reuses the retrieve pipeline."""

    return retrieve_for_widen(state)


def retrieve_for_widen(state: StandardReviewState) -> dict[str, Any]:
    config = load_config().standard_review
    try:
        from standard_document_assistant.review_core.knowledge_base import load_knowledge_base
        kb, kb_meta = load_knowledge_base(config)
    except Exception as exc:
        return {
            "errors": [f"知识库构建失败：{exc}"],
            "trace_events": [emit_event(state, "reload_review_rules", "failed")],
        }
    full_rules = [rule for rule in kb.rules if rule.analysis_mode == "full_document"]
    section_rules: list[RuleItem] = []
    seen_ids: set[str] = set()
    for scope in state.get("active_scope_keys") or []:
        try:
            hits = kb.search(scope, scope=scope, top_k=int(config.top_k), index_dir=str(config.index_dir))
        except Exception:
            hits = []
        for hit in hits:
            if hit.rule.chunk_id in seen_ids or hit.rule.analysis_mode == "full_document":
                continue
            if hit.rule.target_scopes and scope not in hit.rule.target_scopes and hit.rule.scope != scope:
                continue
            seen_ids.add(hit.rule.chunk_id)
            section_rules.append(hit.rule)

    section_rule_dicts = [_rule_to_dict(rule, matched_scope="full_document") for rule in section_rules]
    full_rule_dicts = [_rule_to_dict(rule, matched_scope="full_document") for rule in full_rules]
    return {
        "section_rules": section_rule_dicts,
        "full_document_rules": full_rule_dicts,
        "section_rule_objects": section_rule_dicts,
        "full_document_rule_objects": full_rule_dicts,
        "rules_metadata": {
            **kb_meta,
            "section_rules_count": len(section_rule_dicts),
            "full_document_rules_count": len(full_rule_dicts),
            "partial_mode": "full_document",
            "reloaded_for_round": state.get("review_round"),
        },
        "trace_events": [emit_event(state, "reload_review_rules", "success", {"rules": len(section_rule_dicts) + len(full_rule_dicts)})],
    }


def _rule_to_dict(rule: RuleItem, *, matched_scope: str) -> dict[str, Any]:
    payload = rule.to_dict()
    payload["matched_scope"] = matched_scope
    return payload


def _issue_to_state_payload(issue: AuditIssue) -> dict[str, Any]:
    payload = issue.to_dict()
    payload["audit_track"] = "content_llm"
    payload.setdefault("source_ref", issue.source_ref or "")
    return payload


def _deterministic_judge(state: StandardReviewState, *, config) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    scope_text_map = state.get("scope_text_map") or {}
    section_rules = state.get("section_rules") or []
    issue_no = 1
    for rule in section_rules:
        scope = rule.get("matched_scope") or rule.get("scope") or "full_document"
        text = (scope_text_map.get(scope) or "").strip()
        if text:
            continue
        issue = AuditIssue(
            issue_id=f"LLM-{rule.get('chunk_id') or rule.get('rule_id', '')}-{issue_no:03d}",
            file_name=state.get("content_path", ""),
            rule_id=str(rule.get("chunk_id") or rule.get("rule_id", "")),
            rule_name=str(rule.get("title") or rule.get("rule_name", "")),
            scope=scope,
            severity="中度",
            status="insufficient_context",
            expected=str(rule.get("content") or rule.get("text") or ""),
            actual="未在 Markdown 中定位到对应章节内容。",
            evidence_text="",
            source_ref=str(rule.get("source_ref", "")),
            suggestion="补充对应章节，或人工复核。",
            confidence=0.2,
            llm_reasoning="LLM 内容审核未启用，保留确定性章节存在性检查结果。",
        )
        issue.extras["strategy"] = "deterministic"
        issues.append(_issue_to_state_payload(issue))
        issue_no += 1
    return {
        "issues": issues,
        "trace_events": [emit_event(state, "judge_rules", "deterministic", {"issues": len(issues)})],
    }


def _merge_coverage_check(state: StandardReviewState, result: dict[str, Any]) -> dict[str, Any]:
    """在 ``judge_rules`` 任意返回路径上叠加 scope 覆盖检查的结果。

    覆盖检查：对所有 ``section_rule_objects`` 与 ``full_document_rule_objects`` 的
    ``target_scopes``，若文档没有为该 scope 提供内容（``scope_text_map`` 为空或
    未在 ``active_scope_keys`` 中），则生成一条 ``status="warn"`` 的 issue，
    提示「应当存在但缺失的章节」。该检查不依赖 LLM，确保离线/失败路径仍能
    给出结构性警告。
    """
    coverage = _scope_coverage_check(state)
    if coverage["issues"]:
        result.setdefault("issues", []).extend(coverage["issues"])
    if coverage["trace_events"]:
        result.setdefault("trace_events", []).extend(coverage["trace_events"])
    if coverage["warnings"]:
        result.setdefault("warnings", []).extend(coverage["warnings"])
    return result


def _scope_coverage_check(state: StandardReviewState) -> dict[str, Any]:
    """为「应当存在但缺失」的目标 scope 产生 ``warn`` 级别 issue。

    判定缺失：仅依赖 ``scope_text_map[scope]`` 是否为空字符串（被分类为
    章节后是否解析到对应正文）。``active_scope_keys`` 已包含所有分类的
    scope，不能用作「激活」信号。
    """
    scope_text_map = dict(state.get("scope_text_map") or {})
    content_path = state.get("content_path", "")

    section_rule_dicts = list(state.get("section_rule_objects") or [])
    full_rule_dicts = list(state.get("full_document_rule_objects") or [])
    all_rules = section_rule_dicts + full_rule_dicts

    required_scopes: dict[str, dict[str, Any]] = {}
    for rule in all_rules:
        targets = list(rule.get("target_scopes") or [])
        if not targets and rule.get("scope"):
            targets = [str(rule.get("scope"))]
        for target in targets:
            if target and target not in required_scopes:
                required_scopes[target] = rule

    issues: list[dict[str, Any]] = []
    issue_no = 1
    for scope, rule in sorted(required_scopes.items()):
        text = (scope_text_map.get(scope) or "").strip()
        if text:
            continue
        rule_id = str(rule.get("chunk_id") or rule.get("rule_id") or "")
        title = str(rule.get("title") or rule.get("rule_name") or scope)
        issue = AuditIssue(
            issue_id=f"COVERAGE-{rule_id or scope}-{issue_no:03d}",
            file_name=content_path,
            rule_id=rule_id or f"SCOPE-{scope}",
            rule_name=title,
            scope=scope,
            severity="中度",
            status="warn",
            expected=str(rule.get("content") or rule.get("text") or f"标准应包含 {scope} 章节。"),
            actual=f"未在文档中定位到 {scope} 章节（既无标题也无对应正文）。",
            evidence_text="",
            source_ref=str(rule.get("source_ref", "")),
            suggestion=f"补充 {scope} 章节；若确无相关条目，应在文中显式说明。",
            confidence=0.6,
            llm_reasoning="基于规则 target_scopes 的存在性检查：文档应包含此章节但缺失。",
        )
        issue.extras["strategy"] = "scope_coverage"
        issues.append(_issue_to_state_payload(issue))
        issue_no += 1

    return {
        "issues": issues,
        "warnings": [],
        "trace_events": (
            [
                emit_event(
                    state,
                    "judge_rules",
                    "coverage_check",
                    {"missing_scopes": [issue["scope"] for issue in issues]},
                )
            ]
            if issues
            else []
        ),
    }


# 旧 _event 辅助函数已迁移至 ``standard_document_assistant.graphs.standard_review.events.emit_event``，
# 保留注释说明迁移路径（2026-06-03 rev. 3）：
# - 旧 _event 仅写 state["trace_events"]，未推送流；
# - 新 emit_event 既写 state["trace_events"]，也通过 get_stream_writer 推送 ``review.*`` 事件；
# - payload schema 完全兼容（多 type 字段来自新流式订阅，前端通过 stream_mode="custom" 消费）。
