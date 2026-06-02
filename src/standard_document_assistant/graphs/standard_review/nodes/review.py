"""Content review subgraph: LLM Judge with multi-strategy + quality gate + widen loop."""

from __future__ import annotations

import asyncio
import json
import re
from collections import Counter
from typing import Any, Literal

from langgraph.types import Command

from standard_document_assistant.config import load_config
from standard_document_assistant.graphs.standard_review.state import StandardReviewState
from standard_document_assistant.pathing import utc_now_iso
from standard_document_assistant.review_core.llm_judge import (
    JudgePlan,
    LLMSoftRuleJudge,
    _JUDGE_SYSTEM_PROMPT,
)
from standard_document_assistant.review_core.rule_models import AuditIssue, RuleItem
from standard_document_assistant.review_core.serialization import deserialize_document


def judge_rules(state: StandardReviewState) -> dict[str, Any]:
    if state.get("format_only"):
        return {
            "trace_events": [_event(state, "judge_rules", "skipped")],
        }

    config = load_config().standard_review
    if not config.enable_llm_review:
        return _deterministic_judge(state, config=config)

    try:
        judge = LLMSoftRuleJudge(config)
    except Exception as exc:
        return {
            "errors": [f"LLM Judge 初始化失败：{exc}"],
            "trace_events": [_event(state, "judge_rules", "failed")],
        }

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
        return {
            "warnings": ["未找到可执行的 LLM 审核计划，跳过内容轨。"],
            "trace_events": [_event(state, "judge_rules", "empty")],
        }

    file_name = parsed_payload.get("file_name") or state.get("content_path") or "standard"
    trace_id = state.get("trace_id") or state.get("job_id") or ""
    try:
        outcomes = judge.run_dual_route(plans, file_name=file_name, trace_id=trace_id)
    except Exception as exc:
        return {
            "errors": [f"LLM 审核并发失败：{exc}"],
            "trace_events": [_event(state, "judge_rules", "failed")],
        }

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
    return {
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
            _event(
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


def quality_gate(
    state: StandardReviewState,
) -> Command[Literal["widen_review_scope", "aggregate", "format_review"]]:
    """Decide whether to widen the review scope and rerun the judge.

    Routing logic:
    * if we haven't reached ``max_review_rounds`` and we collected at least
      one ``insufficient_context`` issue whose scope can be expanded to a
      ``full_document`` pass, go to ``widen_review_scope``;
    * otherwise jump to ``aggregate`` (skipping the deterministic format
      review if it was already executed).
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
            update={"trace_events": [_event(state, "quality_gate", "widen")]},
            goto="widen_review_scope",
        )
    next_node = "format_review" if not state.get("format_only") else "aggregate"
    return Command(
        update={"trace_events": [_event(state, "quality_gate", "ok", {"next": next_node})]},
        goto=next_node,
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
        "trace_events": [_event(state, "widen_review_scope", "success", {"round": round_idx})],
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
            "trace_events": [_event(state, "reload_review_rules", "failed")],
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
        "trace_events": [_event(state, "reload_review_rules", "success", {"rules": len(section_rule_dicts) + len(full_rule_dicts)})],
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
        "trace_events": [_event(state, "judge_rules", "deterministic", {"issues": len(issues)})],
    }


def _event(state: StandardReviewState, node: str, status: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "trace_id": state.get("trace_id", ""),
        "job_id": state.get("job_id", ""),
        "component": "standard_review_graph",
        "node": node,
        "event": node,
        "status": status,
        "created_at": utc_now_iso(),
    }
    if extra:
        payload.update(extra)
    return payload
