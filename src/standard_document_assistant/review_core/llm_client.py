"""LLM client wrapper for review-time judge and summary generation.

We rely on LangChain's ChatOpenAI because Qwen DashScope exposes an
OpenAI-compatible endpoint. When the Qwen API key is missing we fall back to
a deterministic fake chat model so the rest of the review pipeline (retrieval,
context chunking, prompt construction, parsing) can be exercised offline.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage


class LLMRequestError(RuntimeError):
    """Raised when the judge LLM cannot return a parseable JSON response."""


@dataclass(frozen=True)
class JudgeSettings:
    provider: str
    model: str
    base_url: str
    api_key: str
    temperature: float
    max_tokens: int
    timeout: int
    max_retries: int
    max_workers: int


class LLMClient:
    """Minimal LLM client that exposes the operations we need."""

    def __init__(self, settings: JudgeSettings, chat_model: BaseChatModel | None = None) -> None:
        self.settings = settings
        self._chat_model = chat_model or self._build_chat_model(settings)

    @classmethod
    def from_env(cls, *, judge_provider: str, judge_model: str, judge_base_url: str,
                 judge_api_key_env: str, judge_temperature: float, judge_max_tokens: int,
                 judge_timeout: int, judge_max_retries: int, judge_max_workers: int) -> "LLMClient":
        api_key = os.getenv(judge_api_key_env) or os.getenv("DASHSCOPE_API_KEY") or ""
        base_url = judge_base_url or os.getenv("DASHSCOPE_BASE_URL") or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        settings = JudgeSettings(
            provider=judge_provider,
            model=judge_model,
            base_url=base_url,
            api_key=api_key,
            temperature=judge_temperature,
            max_tokens=judge_max_tokens,
            timeout=judge_timeout,
            max_retries=judge_max_retries,
            max_workers=judge_max_workers,
        )
        return cls(settings)

    @staticmethod
    def _build_chat_model(settings: JudgeSettings) -> BaseChatModel:
        if not settings.api_key:
            from langchain_core.language_models.fake_chat_models import FakeListChatModel
            return FakeListChatModel(responses=[
                _fake_judge_response(),
                _fake_judge_response(),
            ])
        try:
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(
                model=settings.model,
                base_url=settings.base_url,
                api_key=settings.api_key,
                temperature=settings.temperature,
                max_tokens=settings.max_tokens,
                timeout=settings.timeout,
                max_retries=settings.max_retries,
            )
        except ImportError:
            from langchain_core.language_models.fake_chat_models import FakeListChatModel
            return FakeListChatModel(responses=[_fake_judge_response()])

    def chat(self, messages: list[BaseMessage]) -> str:
        return self._chat_model.invoke(messages).content or ""


def _fake_judge_response() -> str:
    return json.dumps(
        {
            "reasoning": "本地离线模式：根据已提供的规则内容作出保守判定，未调用真实 LLM。",
            "pass": False,
            "severity_level": "中度",
            "actual": "未在文档中直接定位到符合要求的依据，建议人工复核。",
            "evidence_text": "依据不足。",
            "suggestion": "请补充相关章节或重新执行解析与检索。",
            "confidence": 0.2,
        },
        ensure_ascii=False,
    )


def safe_json_loads(text: str) -> dict[str, Any] | None:
    """Best-effort JSON object extraction from a model response."""

    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip("`").strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    snippet = text[start : end + 1]
    try:
        return json.loads(snippet)
    except Exception:
        return None


def build_messages(system_prompt: str, user_prompt: str) -> list[BaseMessage]:
    return [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
