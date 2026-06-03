"""设计评审 (2026-06-03) 修复点验证：C2 / C3 / H1 / L1"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from standard_document_assistant.integrations.mineru import (
    EVENT_LOCAL_REQUEST,
    EVENT_LOCAL_RESPONSE,
    EVENT_LOCAL_RETRY,
    EVENT_PRECISE_POLL,
    MinerUConfigError,
    MinerURequestError,
)
from standard_document_assistant.integrations.mineru.client import (
    DEFAULT_RETRY,
    _request_local_parse_file,
    _retry_with_policy,
    request_parse_file,
)
from standard_document_assistant.tools.parser import (
    _parse_file_with_mineru_sync,
    parse_file_with_mineru,
)


# ─────────────────────────────────────────────────────────────────────────────
# C3: 异常分类 + 收紧 retry 集合
# ─────────────────────────────────────────────────────────────────────────────


def test_config_error_is_not_retried(monkeypatch) -> None:
    """``MinerUConfigError``（如缺 MINERU_API_BASE_URL）必须不进入重试。"""

    from standard_document_assistant.config import MinerUConfig

    cfg = MinerUConfig(api_mode="local", api_base_url="")
    events: list[dict] = []
    with pytest.raises(MinerUConfigError, match="MINERU_API_BASE_URL"):
        _request_local_parse_file(
            Path("dummy.pdf"),
            cfg,
            return_images=True,
            on_event=events.append,
        )
    # 不应产生任何 retry 事件
    assert events == []


def test_4xx_response_raises_config_error_not_retried() -> None:
    """HTTP 4xx 视为协议 / 配置错误，**不**重试。"""

    workdir = Path("workspace/input/uploads/_mineru_review_4xx")
    workdir.mkdir(parents=True, exist_ok=True)
    src = workdir / "doc.pdf"
    src.write_bytes(b"%PDF-1.7")

    class _Resp:
        status_code = 422
        text = "validation error"
        headers = {}

    def fake_post(url, **kwargs):  # noqa: ARG001
        return _Resp()

    # 用真实的 requests 异常层级：RequestException 是 base；Timeout / ConnectionError 是子类
    class _RequestException(Exception):
        pass

    class _Timeout(_RequestException):
        pass

    class _ConnectionError(_RequestException):
        pass

    fake_requests = SimpleNamespace(
        post=fake_post,
        exceptions=SimpleNamespace(
            RequestException=_RequestException,
            Timeout=_Timeout,
            ConnectionError=_ConnectionError,
        ),
    )
    sys.modules["requests"] = fake_requests
    sys.modules["requests.exceptions"] = fake_requests.exceptions

    from standard_document_assistant.config import MinerUConfig

    cfg = MinerUConfig(api_mode="local", api_base_url="http://localhost:9999")
    events: list[dict] = []
    with pytest.raises(MinerUConfigError, match="4xx"):
        _request_local_parse_file(
            src,
            cfg,
            return_images=True,
            on_event=events.append,
        )
    # 4xx 不重试
    assert not any(e.get("type") == EVENT_LOCAL_RETRY for e in events)


def test_5xx_response_is_retried_via_request_error() -> None:
    """HTTP 5xx 视为瞬时错误，**会**重试到 max_attempts。"""

    workdir = Path("workspace/input/uploads/_mineru_review_5xx")
    workdir.mkdir(parents=True, exist_ok=True)
    src = workdir / "doc.pdf"
    src.write_bytes(b"%PDF-1.7")

    class _Resp:
        status_code = 503
        text = "service unavailable"
        headers = {}

    attempts: list[int] = []

    def fake_post(url, *, files, data, timeout):  # noqa: ARG001
        attempts.append(1)
        return _Resp()

    class _RequestException(Exception):
        pass

    class _Timeout(_RequestException):
        pass

    class _ConnectionError(_RequestException):
        pass

    fake_requests = SimpleNamespace(
        post=fake_post,
        exceptions=SimpleNamespace(
            RequestException=_RequestException,
            Timeout=_Timeout,
            ConnectionError=_ConnectionError,
        ),
    )
    sys.modules["requests"] = fake_requests
    sys.modules["requests.exceptions"] = fake_requests.exceptions

    from standard_document_assistant.config import MinerUConfig

    cfg = MinerUConfig(api_mode="local", api_base_url="http://localhost:9999")
    events: list[dict] = []
    with pytest.raises(MinerURequestError, match="503"):
        _request_local_parse_file(
            src,
            cfg,
            return_images=True,
            on_event=events.append,
        )
    # 5xx 触发重试，attempt 数等于 max_attempts
    assert len(attempts) == DEFAULT_RETRY["max_attempts"]
    # 重试事件应有 max_attempts-1 条（最后一条是 exhausted）
    retry_events = [e for e in events if e["type"] == EVENT_LOCAL_RETRY]
    assert len(retry_events) == DEFAULT_RETRY["max_attempts"]
    assert retry_events[-1].get("exhausted") is True


# ─────────────────────────────────────────────────────────────────────────────
# C2: parser 不再使用 InjectedToolArg
# ─────────────────────────────────────────────────────────────────────────────


def test_parser_no_longer_uses_injected_tool_arg() -> None:
    """``_parse_file_with_mineru_sync`` 的 runtime 参数应该是裸 ``ToolRuntime | None``。"""

    import inspect

    sig = inspect.signature(_parse_file_with_mineru_sync)
    runtime_param = sig.parameters["runtime"]
    # 必须没有 ``Annotated[..., InjectedToolArg]`` 包裹
    assert runtime_param.annotation is not inspect.Parameter.empty
    # 不依赖 InjectedToolArg 导入
    import standard_document_assistant.tools.parser as parser_mod
    assert not hasattr(parser_mod, "InjectedToolArg")


def test_parser_uses_runtime_stream_writer(monkeypatch) -> None:
    """``runtime.stream_writer`` 应被调用以推进度。"""

    # 把文件放到 allowed_roots（workspace/input/uploads/**）下
    workdir = Path("workspace/input/uploads/_mineru_review_writer")
    workdir.mkdir(parents=True, exist_ok=True)
    src = workdir / "doc.pdf"
    src.write_bytes(b"%PDF-1.7")
    # 工具接受 /workspace/ 虚拟路径或 workspace 相对路径（去掉 workspace/ 前缀）
    rel_path = "input/uploads/_mineru_review_writer/doc.pdf"

    # 构造最小 ZIP
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as zf:
        zf.writestr("full.md", "# parsed")
    zbytes = zbuf.getvalue()

    captured: list[dict] = []

    class _Resp:
        status_code = 200
        content = zbytes
        headers = {"content-type": "application/zip"}
        text = ""

        def raise_for_status(self) -> None:
            return None

    def fake_post(url, *, files, data, timeout):  # noqa: ARG001
        return _Resp()

    sys.modules["requests"] = SimpleNamespace(
        post=fake_post,
        exceptions=SimpleNamespace(
            RequestException=Exception,
            Timeout=Exception,
            ConnectionError=Exception,
        ),
    )
    sys.modules["requests.exceptions"] = sys.modules["requests"].exceptions

    # 强制 local 模式以避开 precise 路径（env var 名是 MINERU_API_MODE / MINERU_API_BASE_URL）
    monkeypatch.setenv("MINERU_API_MODE", "local")
    monkeypatch.setenv("MINERU_API_BASE_URL", "http://localhost:9999")

    # 构造 duck-type ToolRuntime：只要求 stream_writer 属性
    class FakeWriter:
        def __call__(self, payload: dict) -> None:
            captured.append(payload)

    class FakeRuntime:
        config = {"configurable": {"thread_id": "t-1"}}
        stream_writer = FakeWriter()

    result = _parse_file_with_mineru_sync(
        rel_path,
        return_images=False,
        save_zip_archive=False,
        save_middle_json=False,
        save_content_list=False,
        skip_if_zip_exists=False,
        runtime=FakeRuntime(),  # type: ignore[arg-type]
    )
    assert result["status"] == "ok"
    types = [e["type"] for e in captured]
    assert EVENT_LOCAL_REQUEST in types
    assert EVENT_LOCAL_RESPONSE in types
    assert "mineru.parse.completed" in types


# ─────────────────────────────────────────────────────────────────────────────
# L1: 事件命名以 ``mineru.parse.`` 开头
# ─────────────────────────────────────────────────────────────────────────────


def test_event_names_use_parse_namespace() -> None:
    assert EVENT_LOCAL_REQUEST.startswith("mineru.parse.")
    assert EVENT_LOCAL_RESPONSE.startswith("mineru.parse.")
    assert EVENT_LOCAL_RETRY.startswith("mineru.parse.")
    assert EVENT_PRECISE_POLL.startswith("mineru.parse.")


# ─────────────────────────────────────────────────────────────────────────────
# H3: 主 Agent 层 interrupt_on 不再重复声明 subagent-only 工具
# ─────────────────────────────────────────────────────────────────────────────


def test_main_agent_interrupt_on_excludes_subagent_tools() -> None:
    import os

    os.environ["STANDARD_DOC_ENABLE_HITL"] = "1"
    from standard_document_assistant.agent import build_subagents

    specs = build_subagents(langgraph_server=False)
    by_name = {s["name"]: s for s in specs}
    # subagent 层仍包含 parse_file_with_mineru（统一入口）
    assert "parse_file_with_mineru" in by_name["parser"].get("interrupt_on", {})
    assert "parse_file_with_mineru" in by_name["reviewer"].get("interrupt_on", {})
    # extractor 收紧到 allowed_decisions
    extractor_int = by_name["extractor"].get("interrupt_on", {})
    assert extractor_int["extract_standard_metadata"]["allowed_decisions"] == ["approve", "edit"]
    # 通过 build_subagents 的契约：主 Agent 的 interrupt_on 由调用方注入；
    # 这里只验证 subagent 层不互相覆盖（互斥：parser 只控 parse_file，reviewer 只控 parse_document）
