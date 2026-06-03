"""针对 MinerU 重试 / 进度回调 / async 包装的轻量测试。"""

from __future__ import annotations

import asyncio
import io
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from standard_document_assistant.config import MinerUConfig
from standard_document_assistant.integrations.mineru.client import (
    DEFAULT_RETRY,
    EVENT_LOCAL_RETRY,
    EVENT_LOCAL_REQUEST,
    EVENT_LOCAL_RESPONSE,
    EVENT_PRECISE_POLL,
    _retry_with_policy,
    request_parse_file,
)
from standard_document_assistant.tools.parser import (
    _parse_file_with_mineru_async,
    parse_file_with_mineru,
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. 重试 + 进度回调
# ─────────────────────────────────────────────────────────────────────────────


def test_retry_with_policy_recovers_after_two_failures() -> None:
    attempts: list[int] = []

    def flaky() -> str:
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("transient")
        return "ok"

    events: list[dict] = []
    res = _retry_with_policy(
        flaky,
        description="unit",
        on_event=events.append,
        config={"max_attempts": 3, "initial_interval": 0.001, "max_interval": 0.01, "jitter": False},
    )
    assert res == "ok"
    assert len(attempts) == 3
    assert [e["type"] for e in events] == [EVENT_LOCAL_RETRY, EVENT_LOCAL_RETRY]
    assert events[0]["attempt"] == 1
    assert events[1]["attempt"] == 2
    # 第二次重试 backoff 应大于等于第一次
    assert events[1]["next_interval_s"] >= events[0]["next_interval_s"]


def test_retry_with_policy_exhausts_and_raises() -> None:
    def always_fail() -> None:
        raise RuntimeError("boom")

    events: list[dict] = []
    with pytest.raises(RuntimeError, match="boom"):
        _retry_with_policy(
            always_fail,
            description="unit",
            on_event=events.append,
            config={"max_attempts": 2, "initial_interval": 0.001, "max_interval": 0.01, "jitter": False},
        )
    # 两次失败：第一次 retry 事件 + 第二次 exhausted 事件
    assert [e["type"] for e in events] == [EVENT_LOCAL_RETRY, EVENT_LOCAL_RETRY]
    assert events[-1]["exhausted"] is True


def test_default_retry_matches_design_doc() -> None:
    # 设计文档 §3.3 item 2: max_attempts=3, initial_interval=2.0, max_interval=10.0, jitter=True
    assert DEFAULT_RETRY["max_attempts"] == 3
    assert DEFAULT_RETRY["initial_interval"] == 2.0
    assert DEFAULT_RETRY["max_interval"] == 10.0
    assert DEFAULT_RETRY["jitter"] is True


# ─────────────────────────────────────────────────────────────────────────────
# 2. local 模式：进度事件 + 错误透出
# ─────────────────────────────────────────────────────────────────────────────


def test_local_request_emits_request_and_response_events() -> None:
    # 故意不依赖 pytest 的 tmp_path（Windows 沙箱里 C:\Users\... 经常无写权限）
    workdir = Path("workspace/tmp/_mineru_test_local")
    workdir.mkdir(parents=True, exist_ok=True)
    src = workdir / "doc.pdf"
    src.write_bytes(b"%PDF-1.7")
    zbytes = io.BytesIO()
    with zipfile.ZipFile(zbytes, "w") as zf:
        zf.writestr("full.md", "# parsed")

    class _Resp:
        def __init__(self) -> None:
            self.content = zbytes.getvalue()
            self.status_code = 200
            self.headers = {"content-type": "application/zip"}
            self.text = ""

        def raise_for_status(self) -> None:
            return None

    captured: list[dict] = []

    def fake_post(url, *, files, data, timeout):  # noqa: ARG001
        return _Resp()

    fake_requests = SimpleNamespace(
        post=fake_post,
        exceptions=SimpleNamespace(
            RequestException=Exception,
            Timeout=Exception,
            ConnectionError=Exception,
        ),
    )
    sys.modules["requests"] = fake_requests

    cfg = MinerUConfig(api_mode="local", api_base_url="http://localhost:9999")
    result = request_parse_file(src, cfg, return_images=True, on_event=captured.append)

    assert [e["type"] for e in captured] == [EVENT_LOCAL_REQUEST, EVENT_LOCAL_RESPONSE]
    assert captured[0]["url"].endswith("/file_parse")
    assert captured[1]["status_code"] == 200
    assert captured[1]["size_bytes"] > 0
    assert result == zbytes.getvalue()
    # 清理
    try:
        src.unlink()
    except OSError:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# 3. async 包装：coroutine attribute + asyncio.to_thread 路径
# ─────────────────────────────────────────────────────────────────────────────


def test_parse_file_with_mineru_exposes_async_coroutine() -> None:
    # StructuredTool.from_function(..., coroutine=...) 设置了 .coroutine 属性
    assert parse_file_with_mineru.coroutine is _parse_file_with_mineru_async
    assert asyncio.iscoroutinefunction(_parse_file_with_mineru_async)


def test_async_wrapper_runs_sync_impl_in_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """``asyncio.to_thread`` 应当把同步实现丢到工作线程，事件循环不被阻塞。"""

    import threading
    import time

    import standard_document_assistant.tools.parser as parser_module

    main_thread_id = threading.get_ident()
    observed: list[int] = []

    def fake_sync(file_path, **kwargs):  # noqa: ARG001
        # 用一段很短的 sleep，模拟 to_thread 切换上下文
        time.sleep(0.01)
        observed.append(threading.get_ident())
        return {"status": "ok", "file_path": file_path}

    monkeypatch.setattr(parser_module, "_parse_file_with_mineru_sync", fake_sync)

    async def _drive() -> dict:
        # 如果不是 to_thread 跑，事件循环会被 sleep 阻塞（虽然此处只有 10ms）
        return await _parse_file_with_mineru_async("/workspace/input/uploads/sample.pdf")

    res = asyncio.run(_drive())
    assert res["status"] == "ok"
    # 实际执行线程应与主线程不同
    assert observed, "fake_sync 没被调用"
    assert observed[0] != main_thread_id
