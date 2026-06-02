"""Retrieve subgraph for standard review (FAISS + 全文规则)."""

from __future__ import annotations

from typing import Any

from standard_document_assistant.config import load_config
from standard_document_assistant.constants import PROJECT_ROOT
from standard_document_assistant.graphs.standard_review.state import StandardReviewState
from standard_document_assistant.pathing import utc_now_iso
from standard_document_assistant.review_core.knowledge_base import (
    filter_content_audit_rules,
    load_knowledge_base,
)
from standard_document_assistant.review_core.rule_models import RuleItem
from standard_document_assistant.review_core.scopes import filter_rules_for_partial_mode, normalize_scope_keys


def retrieve_rules(state: StandardReviewState) -> dict[str, Any]:
    config = load_config().standard_review
    force = bool(state.get("force_rebuild_index")) or bool(config.auto_rebuild_index)
    try:
        kb, kb_meta = load_knowledge_base(config, force_rebuild=False)
        if force and kb_meta.get("index_source") != "rebuilt":
            kb, kb_meta = load_knowledge_base(config, force_rebuild=True)
    except Exception as exc:
        return {
            "errors": [f"知识库构建失败：{exc}"],
            "trace_events": [_event(state, "retrieve_rules", "failed")],
        }

    rules = filter_content_audit_rules(kb.rules)
    top_k = int(state.get("top_k") or config.top_k)
    requested = normalize_scope_keys(state.get("target_scopes"))
    active_keys = list(state.get("active_scope_keys") or [])
    partial_mode = state.get("partial_mode") or "sectional"
    if partial_mode == "full_document":
        active_keys = ["cover", "toc", "foreword", "scope", "normative_references", "terms_definitions", "other_body", "appendix", "end"]
    if requested:
        active_keys = [key for key in active_keys if key in set(requested)] or active_keys

    section_rules: list[RuleItem] = []
    section_rule_dicts: list[dict[str, Any]] = []
    retrieval_trace: list[dict[str, Any]] = []
    index_dir = str((PROJECT_ROOT / config.index_dir).resolve())

    if partial_mode == "full_document":
        for rule in rules:
            if rule.analysis_mode == "full_document":
                section_rules.append(rule)
                section_rule_dicts.append(_rule_to_dict(rule, matched_scope="full_document"))
        retrieval_trace.append(
            {
                "scope": "full_document",
                "top_k": top_k,
                "rule_ids": [rule.chunk_id for rule in section_rules],
                "query": "full_document:overall",
            }
        )
    else:
        seen_ids: set[str] = set()
        for scope_key in active_keys:
            try:
                hits = kb.search(scope_key, scope=scope_key, top_k=top_k, index_dir=index_dir)
            except Exception:
                hits = []
            for hit in hits:
                if hit.rule.chunk_id in seen_ids:
                    continue
                if hit.rule.analysis_mode == "full_document":
                    continue
                if hit.rule.target_scopes and scope_key not in hit.rule.target_scopes:
                    if hit.rule.scope != scope_key:
                        continue
                seen_ids.add(hit.rule.chunk_id)
                section_rules.append(hit.rule)
                section_rule_dicts.append(_rule_to_dict(hit.rule, matched_scope=scope_key))
            retrieval_trace.append(
                {
                    "scope": scope_key,
                    "top_k": top_k,
                    "rule_ids": [hit.rule.chunk_id for hit in hits],
                    "query": f"scope:{scope_key}",
                }
            )

    full_doc_rules = [rule for rule in rules if rule.analysis_mode == "full_document"]
    full_rule_dicts = [_rule_to_dict(rule, matched_scope="full_document") for rule in full_doc_rules]

    return {
        "section_rules": section_rule_dicts,
        "full_document_rules": full_rule_dicts,
        "section_rule_objects": section_rule_dicts,
        "full_document_rule_objects": full_rule_dicts,
        "retrieval_trace": retrieval_trace,
        "rules_metadata": {
            **kb_meta,
            "section_rules_count": len(section_rules),
            "full_document_rules_count": len(full_doc_rules),
            "active_scope_keys": list(active_keys),
            "partial_mode": partial_mode,
        },
        "trace_events": [
            _event(
                state,
                "retrieve_rules",
                "success",
                {"section_rules": len(section_rule_dicts), "full_rules": len(full_rule_dicts)},
            )
        ],
    }


def _rule_to_dict(rule: RuleItem, *, matched_scope: str) -> dict[str, Any]:
    payload = rule.to_dict()
    payload["matched_scope"] = matched_scope
    return payload


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
