"""LLM-as-a-Judge for standard document content rules.

Implements the multi-strategy judge used by the standard review graph:

* ``single``  - one rule + one focused context window
* ``window``  - batch several rules against the same windowed context
* ``cross_section`` - batch rules whose target spans multiple sections
* ``full_document`` - retry the whole document with all P0 rules

The judge mirrors the LLM judge in ``Chinese_national_standards_docs_Review-SKILL``
but is rewritten against Deep Agents' preferred patterns: tool-callable
``judge_rules`` (returns ``AuditIssue`` list) and emits ``custom`` events
through the LangGraph stream writer.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

from langgraph.config import get_stream_writer

from standard_document_assistant.config import StandardReviewConfig
from standard_document_assistant.review_core.context_chunker import DocumentContextBuilder
from standard_document_assistant.review_core.llm_client import LLMClient, safe_json_loads
from standard_document_assistant.review_core.rule_models import AuditIssue, RuleItem


@dataclass
class JudgePlan:
    rule: RuleItem
    strategy: str
    context_text: str
    meta: dict[str, Any]


@dataclass
class JudgeOutcome:
    rule: RuleItem
    strategy: str
    issue: AuditIssue | None
    raw: str
    skipped: bool = False
    notes: list[str] | None = None


_JUDGE_SYSTEM_PROMPT = (
    "你是一名国家级标准文档审核员，必须严格依据所给的「规则」对文档进行判定。"
    "只输出 JSON，禁止任何解释、Markdown 包裹或多余文本。\n"
    "JSON 字段：\n"
    "  pass (bool): 是否通过该规则；\n"
    "  severity_level (str): 严重程度（致命/中度/轻度），缺省为'中度'；\n"
    "  actual (str): 文档中的实际情况（1-2 句中文）；\n"
    "  evidence_text (str): 1-3 句引用文档原文作为证据；\n"
    "  suggestion (str): 改进建议；\n"
    "  reasoning (str): 100-200 字的判定理由；\n"
    "  confidence (float, 0-1): 置信度，0.2=证据缺失，0.5=部分依据，0.85=强依据；\n"
    "当证据不足时设置 pass=false、confidence<=0.4、status='insufficient_context'，"
    "并在 evidence_text 中说明'依据不足'。"
)


class LLMSoftRuleJudge:
    """Multi-strategy LLM judge for soft rules."""

    def __init__(self, config: StandardReviewConfig, llm_client: LLMClient | None = None) -> None:
        self.config = config
        self.client = llm_client or LLMClient.from_env(
            judge_provider=config.judge_provider,
            judge_model=config.judge_model,
            judge_base_url=config.judge_base_url,
            judge_api_key_env=config.judge_api_key_env,
            judge_temperature=config.judge_temperature,
            judge_max_tokens=config.judge_max_tokens,
            judge_timeout=config.judge_timeout,
            judge_max_retries=config.judge_max_retries,
            judge_max_workers=config.judge_max_workers,
        )
        self.context_builder = DocumentContextBuilder(config)

    def plan(self, rules: list[RuleItem], document, scope_text_map: dict[str, str]) -> list[JudgePlan]:
        plans: list[JudgePlan] = []
        for rule in rules:
            ctx, meta = self.context_builder.build_rule_context(rule, document, scope_text_map)
            strategy = self._choose_strategy(rule, len(ctx))
            if strategy == "skip":
                continue
            plans.append(JudgePlan(rule=rule, strategy=strategy, context_text=ctx, meta=meta))
        return plans

    async def arun_dual_route(
        self,
        plans: list[JudgePlan],
        *,
        file_name: str,
        trace_id: str,
    ) -> list[JudgeOutcome]:
        sem = asyncio.Semaphore(max(1, int(self.config.judge_max_workers)))
        tasks = [self._judge_with_sem(sem, plan, file_name=file_name, trace_id=trace_id) for plan in plans]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        outcomes: list[JudgeOutcome] = []
        for plan, result in zip(plans, results):
            if isinstance(result, Exception):
                outcomes.append(
                    JudgeOutcome(
                        rule=plan.rule,
                        strategy=plan.strategy,
                        issue=self._fallback_issue(plan, file_name=trace_id, error=str(result)),
                        raw="",
                    )
                )
                continue
            outcomes.append(result)
        return outcomes

    def run_dual_route(
        self,
        plans: list[JudgePlan],
        *,
        file_name: str,
        trace_id: str,
    ) -> list[JudgeOutcome]:
        return asyncio.run(self.arun_dual_route(plans, file_name=file_name, trace_id=trace_id))

    async def _judge_with_sem(self, sem, plan: JudgePlan, *, file_name: str, trace_id: str) -> JudgeOutcome:
        async with sem:
            writer = get_stream_writer()
            writer({"type": "scope_progress", "rule": plan.rule.chunk_id, "strategy": plan.strategy})
            try:
                payload = await self._invoke_strategy(plan)
            except Exception as exc:  # pragma: no cover - defensive
                writer({"type": "judge_error", "rule": plan.rule.chunk_id, "error": str(exc)})
                return JudgeOutcome(
                    rule=plan.rule,
                    strategy=plan.strategy,
                    issue=self._fallback_issue(plan, file_name=trace_id, error=str(exc)),
                    raw="",
                )
            issue = self._build_issue(plan, payload, file_name=file_name)
            return JudgeOutcome(rule=plan.rule, strategy=plan.strategy, issue=issue, raw=json.dumps(payload, ensure_ascii=False))

    async def _invoke_strategy(self, plan: JudgePlan) -> dict[str, Any]:
        if plan.strategy == "single":
            return await self._call_single(plan)
        if plan.strategy == "window":
            return await self._call_windowed(plan)
        if plan.strategy == "cross_section":
            return await self._call_cross_section(plan)
        if plan.strategy == "full_document":
            return await self._call_full_document(plan)
        return await self._call_single(plan)

    async def _call_single(self, plan: JudgePlan) -> dict[str, Any]:
        user_prompt = self._build_single_prompt(plan)
        response = await asyncio.to_thread(
            self.client.chat,
            [_system_message(), _human_message(user_prompt)],
        )
        return safe_json_loads(response) or _empty_result("无法解析 LLM 响应")

    async def _call_windowed(self, plan: JudgePlan) -> dict[str, Any]:
        user_prompt = self._build_windowed_prompt(plan)
        response = await asyncio.to_thread(
            self.client.chat,
            [_system_message(), _human_message(user_prompt)],
        )
        return safe_json_loads(response) or _empty_result("无法解析 LLM 响应")

    async def _call_cross_section(self, plan: JudgePlan) -> dict[str, Any]:
        user_prompt = self._build_cross_section_prompt(plan)
        response = await asyncio.to_thread(
            self.client.chat,
            [_system_message(), _human_message(user_prompt)],
        )
        return safe_json_loads(response) or _empty_result("无法解析 LLM 响应")

    async def _call_full_document(self, plan: JudgePlan) -> dict[str, Any]:
        user_prompt = self._build_full_document_prompt(plan)
        response = await asyncio.to_thread(
            self.client.chat,
            [_system_message(), _human_message(user_prompt)],
        )
        return safe_json_loads(response) or _empty_result("无法解析 LLM 响应")

    def _build_single_prompt(self, plan: JudgePlan) -> str:
        return (
            f"# 规则\n标题：{plan.rule.title}\n规则 ID：{plan.rule.chunk_id}\n"
            f"适用范围：{plan.rule.scope}\n"
            f"内容：\n{plan.rule.content}\n\n"
            f"# 待审核文档片段\n{plan.context_text}\n\n"
            "请按系统提示中要求的 JSON 字段输出。"
        )

    def _build_windowed_prompt(self, plan: JudgePlan) -> str:
        related = plan.meta.get("related_rules", [])
        related_lines = "\n".join(
            f"- {item.get('rule_id')}: {item.get('title')}" for item in related
        ) or "（无关联规则）"
        return (
            f"# 规则\n{plan.rule.title} ({plan.rule.chunk_id})\n{plan.rule.content}\n\n"
            f"# 关联规则（用于交叉验证）\n{related_lines}\n\n"
            f"# 文档窗口内容\n{plan.context_text}\n\n"
            "请按系统提示中要求的 JSON 字段输出。"
        )

    def _build_cross_section_prompt(self, plan: JudgePlan) -> str:
        return (
            f"# 跨节规则\n{plan.rule.title} ({plan.rule.chunk_id})\n"
            f"要求章节：{', '.join(plan.rule.target_scopes or [plan.rule.scope])}\n"
            f"规则内容：{plan.rule.content}\n\n"
            f"# 多节上下文\n{plan.context_text}\n\n"
            "请按系统提示中要求的 JSON 字段输出。"
        )

    def _build_full_document_prompt(self, plan: JudgePlan) -> str:
        return (
            f"# 全文级规则\n{plan.rule.title} ({plan.rule.chunk_id})\n{plan.rule.content}\n\n"
            f"# 全文文档（截取前 {len(plan.context_text)} 字符）\n{plan.context_text}\n\n"
            "请按系统提示中要求的 JSON 字段输出。"
        )

    def _choose_strategy(self, rule: RuleItem, context_len: int) -> str:
        if rule.analysis_mode == "full_document":
            return "full_document"
        if rule.analysis_mode == "cross_section":
            return "cross_section"
        if rule.analysis_mode == "deterministic":
            return "skip"
        if context_len < self.config.min_context_chars_local:
            return "skip"
        if rule.analysis_mode == "local" and context_len > self.config.batch_scope_max_chars:
            return "window"
        return "single"

    def _build_issue(self, plan: JudgePlan, payload: dict[str, Any] | None, *, file_name: str) -> AuditIssue | None:
        if not payload:
            return self._fallback_issue(plan, file_name=file_name, error="无响应", status="insufficient_context")
        try:
            passed = bool(payload.get("pass"))
        except Exception:
            passed = False
        if passed:
            return None
        try:
            confidence = float(payload.get("confidence", 0.0))
        except Exception:
            confidence = 0.0
        severity = str(payload.get("severity_level") or payload.get("severity") or "中度")
        actual = str(payload.get("actual") or "未在文档中明确说明")
        evidence = str(payload.get("evidence_text") or "")
        suggestion = str(payload.get("suggestion") or "")
        reasoning = str(payload.get("reasoning") or "")
        status = "fail" if confidence > self.config.low_confidence_floor else "insufficient_context"
        issue = AuditIssue(
            issue_id=f"{plan.rule.chunk_id}-{file_name}",
            file_name=file_name,
            rule_id=plan.rule.chunk_id,
            rule_name=plan.rule.title,
            scope=plan.rule.scope,
            severity=severity,
            status=status,
            expected=plan.rule.content,
            actual=actual,
            evidence_text=evidence,
            source_ref=plan.rule.source_ref,
            suggestion=suggestion,
            confidence=confidence,
            llm_reasoning=reasoning,
        )
        issue.extras["strategy"] = plan.strategy
        return issue

    def _fallback_issue(self, plan: JudgePlan, *, file_name: str, error: str, status: str = "fail") -> AuditIssue:
        return AuditIssue(
            issue_id=f"{plan.rule.chunk_id}-{file_name}-fallback",
            file_name=file_name,
            rule_id=plan.rule.chunk_id,
            rule_name=plan.rule.title,
            scope=plan.rule.scope,
            severity="轻度",
            status=status,
            expected=plan.rule.content,
            actual=f"LLM 审核异常：{error[:200]}",
            evidence_text="依据不足。",
            source_ref=plan.rule.source_ref,
            suggestion="请人工复核此条规则。",
            confidence=0.1,
            llm_reasoning=f"异常降级：{error}",
        )


def _system_message():
    from langchain_core.messages import SystemMessage
    return SystemMessage(content=_JUDGE_SYSTEM_PROMPT)


def _human_message(text: str):
    from langchain_core.messages import HumanMessage
    return HumanMessage(content=text)


def _empty_result(reason: str) -> dict[str, Any]:
    return {
        "pass": False,
        "severity_level": "轻度",
        "actual": reason,
        "evidence_text": "依据不足。",
        "suggestion": "请人工复核。",
        "reasoning": reason,
        "confidence": 0.1,
    }
