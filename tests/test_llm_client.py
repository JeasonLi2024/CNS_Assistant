import pytest

from standard_document_assistant.review_core.llm_client import LLMClient, JudgeSettings, safe_json_loads


class FailingChatModel:
    def invoke(self, messages):
        raise RuntimeError("connection denied")


def _settings() -> JudgeSettings:
    return JudgeSettings(
        provider="dashscope-compatible",
        model="qwen3.7-plus",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="test-key",
        temperature=0.0,
        max_tokens=128,
        timeout=1,
        max_retries=0,
        max_workers=1,
    )


def test_llm_client_raises_connection_errors_by_default(monkeypatch) -> None:
    monkeypatch.delenv("STANDARD_DOC_LLM_OFFLINE_FALLBACK", raising=False)
    client = LLMClient(_settings(), chat_model=FailingChatModel())

    with pytest.raises(RuntimeError, match="connection denied"):
        client.chat([])


def test_llm_client_offline_fallback_returns_parseable_judge_json(monkeypatch) -> None:
    monkeypatch.setenv("STANDARD_DOC_LLM_OFFLINE_FALLBACK", "1")
    client = LLMClient(_settings(), chat_model=FailingChatModel())

    payload = safe_json_loads(client.chat([]))

    assert payload is not None
    assert payload["pass"] is False
    assert "本地离线降级" in payload["reasoning"]
    assert "LLM Judge 连接不可用" in payload["actual"]
