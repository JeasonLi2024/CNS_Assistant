"""LLM-generated audit summary for the final audit report."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from standard_document_assistant.config import StandardReviewConfig
from standard_document_assistant.review_core.llm_client import LLMClient, safe_json_loads
from standard_document_assistant.review_core.rule_models import AuditIssue


_SUMMARY_SYSTEM_PROMPT = (
    "你是国家级标准审核报告摘要生成助手。请根据结构化的审核问题清单，"
    "输出一份不超过 600 字的中文执行摘要。摘要应包含：1) 总体评价；"
    "2) 关键风险章节；3) 优先级整改建议；4) 不确定/依据不足事项。\n"
    "输出 JSON 字段：summary (str, <= 600 字)、key_risks (list[str])、"
    "top_fixes (list[str])、insufficient (list[str])。禁止任何解释或额外文本。"
)


@dataclass
class AuditSummaryResult:
    summary: str
    key_risks: list[str]
    top_fixes: list[str]
    insufficient: list[str]
    raw: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "key_risks": list(self.key_risks),
            "top_fixes": list(self.top_fixes),
            "insufficient": list(self.insufficient),
        }


def generate_audit_summary(
    issues: list[AuditIssue],
    *,
    file_name: str,
    config: StandardReviewConfig,
    client: LLMClient | None = None,
) -> AuditSummaryResult | None:
    if not config.enable_audit_summary:
        return None
    if not issues:
        return AuditSummaryResult(
            summary="审核未发现重大问题，文档结构与基本规范性条目满足要求。",
            key_risks=[],
            top_fixes=[],
            insufficient=[],
            raw="",
        )
    summary_client = client or _build_summary_client(config)
    issue_dicts = [issue.to_dict() for issue in issues[:32]]
    user_prompt = (
        f"文档：{file_name}\n问题数量：{len(issues)}\n"
        "以下是结构化问题清单（按严重程度排序）：\n"
        f"{json.dumps(issue_dicts, ensure_ascii=False, indent=2)}"
    )
    try:
        response = summary_client.chat(
            [SystemMessage(content=_SUMMARY_SYSTEM_PROMPT), HumanMessage(content=user_prompt)]
        )
    except Exception as exc:  # pragma: no cover - offline fallback
        return _offline_summary(issues, raw=str(exc))
    payload = safe_json_loads(response) or {}
    if not payload or not payload.get("summary"):
        # No real LLM (FakeListChatModel returns empty string). Build offline summary.
        return _offline_summary(issues, raw=response)
    return AuditSummaryResult(
        summary=str(payload.get("summary") or "").strip()[: config.summary_max_chars],
        key_risks=[str(item) for item in payload.get("key_risks", [])][:6],
        top_fixes=[str(item) for item in payload.get("top_fixes", [])][:6],
        insufficient=[str(item) for item in payload.get("insufficient", [])][:6],
        raw=response,
    )


def _offline_summary(issues, *, raw: str) -> AuditSummaryResult:
    """Deterministic summary fallback (no LLM)."""

    total = len(issues)
    severities: dict[str, int] = {}
    scopes: dict[str, int] = {}
    insufficient: list[str] = []
    for issue in issues:
        severities[issue.severity] = severities.get(issue.severity, 0) + 1
        scopes[issue.scope] = scopes.get(issue.scope, 0) + 1
        if issue.status == "insufficient_context" and issue.rule_name not in insufficient:
            insufficient.append(issue.rule_name)
    top_scope = sorted(scopes.items(), key=lambda x: x[1], reverse=True)[:3]
    top_scope_text = "、".join(f"{s}（{c} 项）" for s, c in top_scope) or "无"
    summary = (
        f"共发现 {total} 项潜在风险。"
        f"严重级别分布：{', '.join(f'{k} {v}' for k, v in severities.items()) or '无'}；"
        f"主要受影响章节：{top_scope_text}。"
        "建议优先复核以上章节并按 LLM 给出的建议整改；离线模式下未生成 LLM 摘要，仅作自动汇总。"
    )
    return AuditSummaryResult(
        summary=summary,
        key_risks=[f"{scope}（{count} 项）" for scope, count in top_scope],
        top_fixes=[issue.suggestion for issue in issues[:6] if issue.suggestion],
        insufficient=insufficient[:6],
        raw=raw,
    )


def _build_summary_client(config: StandardReviewConfig) -> LLMClient:
    api_key = os.getenv(config.judge_api_key_env) or os.getenv("DASHSCOPE_API_KEY") or ""
    if not api_key:
        return LLMClient.from_env(
            judge_provider=config.judge_provider,
            judge_model=config.summary_model,
            judge_base_url=config.judge_base_url,
            judge_api_key_env=config.judge_api_key_env,
            judge_temperature=config.judge_temperature,
            judge_max_tokens=config.judge_max_tokens,
            judge_timeout=config.judge_timeout,
            judge_max_retries=config.judge_max_retries,
            judge_max_workers=config.judge_max_workers,
        )
    return LLMClient.from_env(
        judge_provider=config.judge_provider,
        judge_model=config.summary_model,
        judge_base_url=config.judge_base_url,
        judge_api_key_env=config.judge_api_key_env,
        judge_temperature=config.judge_temperature,
        judge_max_tokens=config.judge_max_tokens,
        judge_timeout=config.judge_timeout,
        judge_max_retries=config.judge_max_retries,
        judge_max_workers=config.judge_max_workers,
    )
