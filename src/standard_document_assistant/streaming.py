"""SSE event helpers for Deep Agents runs."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING, Any

from langgraph.types import Command

if TYPE_CHECKING:
    from standard_document_assistant.agent import build_thread_config
    from standard_document_assistant.artifacts import (
        public_artifact_record,
        register_artifacts_from_messages,
        register_from_tool_result,
    )

from standard_document_assistant.pathing import utc_now_iso


def _agent_build_thread_config(thread_id: str) -> Any:
    """Lazy wrapper for :func:`agent.build_thread_config` to break circular imports.

    ``agent.py`` 顶层 ``from standard_document_assistant.tools import (...)``，
    而 ``tools/review.py`` 又通过 ``graphs.standard_review.events`` 引用本文件；
    顶层 import 会触发循环。这里把 ``agent`` 的 import 推迟到首次调用。
    """

    from standard_document_assistant.agent import build_thread_config

    return build_thread_config(thread_id)


def _artifacts_public_record(record: Any) -> Any:
    from standard_document_assistant.artifacts import public_artifact_record

    return public_artifact_record(record)


def _artifacts_register_from_tool_result(*args: Any, **kwargs: Any) -> Any:
    from standard_document_assistant.artifacts import register_from_tool_result

    return register_from_tool_result(*args, **kwargs)


def _artifacts_register_from_messages(*args: Any, **kwargs: Any) -> Any:
    from standard_document_assistant.artifacts import register_artifacts_from_messages

    return register_artifacts_from_messages(*args, **kwargs)


# ---------------------------------------------------------------------------
# 统一流式事件 payload（2026-06-03 rev. 4）
# ---------------------------------------------------------------------------
# 三个模块（parser / metadata_extraction_graph / standard_review_graph）以及
# 工具层（tools/parser.py、tools/review.py、tools/metadata.py）共用同一份
# ``<domain>.<stage>`` 命名规范和最小公共字段。依据
# [docs-langchain Stream writer](https://docs.langchain.com/oss/python/langchain/tools#stream-writer)：
#
# - ``type``      必填，形如 ``mineru.parse.completed`` / ``meta.scoped`` /
#                 ``review.judge.success`` / ``review.tool.start``。
# - ``trace_id``  横切关注点：来自父 agent Runtime context 或 state；空字符串合法。
# - ``component`` 发起组件名（"parser" / "metadata_extraction_graph" /
#                 "standard_review_graph" / "standard_review_tool"），便于前端按
#                 component 着色 / 过滤。
# - ``created_at`` ISO 8601 时间戳，便于跨事件排序。
# - 业务字段（duration_ms / issues / plans / zip_cache_hit 等）通过 ``**extra``
#   自由追加，与既有 consumer 兼容。
#
# 设计动机：旧三模块 payload 形状不一（review 有 trace_id / job_id / component；
# langextract / mineru 没有），前端按 ``type`` 字段可正常消费但日志聚合缺字段。
# 现在统一为上述最小公共 schema，**纯新增** 字段（不删除既有字段），向后兼容。


def make_event_payload(
    event_type: str,
    *,
    trace_id: str = "",
    component: str = "",
    job_id: str = "",
    **extra: Any,
) -> dict[str, Any]:
    """构造统一的流式事件 payload。

    Parameters
    ----------
    event_type : str
        形如 ``mineru.parse.completed`` / ``meta.scoped`` / ``review.judge.success``。
    trace_id : str
        父 agent 透传的 trace 关联 ID（可空字符串）。
    component : str
        发起组件名（"parser" / "metadata_extraction_graph" /
        "standard_review_graph" / "standard_review_tool"）。
    job_id : str
        任务 ID（langextract 模块未使用，置空即可）。
    **extra
        业务字段（如 duration_ms / plans / issues 等），不与公共字段冲突。
    """

    payload: dict[str, Any] = {
        "type": event_type,
        "trace_id": trace_id or "",
        "component": component or "",
        "created_at": utc_now_iso(),
    }
    if job_id:
        payload["job_id"] = job_id
    for key, value in extra.items():
        # 不覆盖公共字段
        if key in {"type", "trace_id", "component", "created_at", "job_id"}:
            continue
        payload[key] = value
    return payload


def safe_stream_writer() -> Any | None:
    """获取 :func:`langgraph.config.get_stream_writer`，图外调用返回 None。

    节点 / 工具内调用 ``get_stream_writer()``，单测 / Tool 直调可能抛
    ``RuntimeError``，这里统一吞掉，返回 ``None`` 表示无 writer。
    """

    try:
        from langgraph.config import get_stream_writer

        return get_stream_writer()
    except (RuntimeError, AssertionError, ImportError):
        return None


def emit_stream_event(
    event_type: str,
    *,
    trace_id: str = "",
    component: str = "",
    job_id: str = "",
    writer: Any | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """构造统一 payload 并通过 ``stream_writer`` 推送。

    返回构造好的 payload 字典，便于调用方继续 append 到 ``state["trace_events"]``
    等本地累加器。失败（无 writer / 序列化错）静默吞掉，不影响主流程。
    """

    payload = make_event_payload(
        event_type,
        trace_id=trace_id,
        component=component,
        job_id=job_id,
        **extra,
    )
    target_writer = writer if writer is not None else safe_stream_writer()
    if target_writer is not None:
        try:
            target_writer(payload)
        except (RuntimeError, TypeError, ValueError, AttributeError, KeyError):
            pass
    return payload


def sse_encode(event: str, data: dict[str, Any]) -> str:
    """Encode one Server-Sent Event frame."""

    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def artifact_created_events(
    *,
    run_id: str,
    thread_id: str,
    records: list[Any],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for record in records:
        payload = _artifacts_public_record(record)
        events.append(
            {
                "event": "artifact.created",
                "data": {
                    "run_id": run_id,
                    "thread_id": thread_id,
                    **payload,
                },
            }
        )
    return events


def map_tool_completed(
    *,
    run_id: str,
    thread_id: str,
    tool_name: str,
    tool_result: dict[str, Any],
) -> list[dict[str, Any]]:
    records = _artifacts_register_from_tool_result(
        thread_id=thread_id,
        tool_name=tool_name,
        tool_result=tool_result,
    )
    events = artifact_created_events(run_id=run_id, thread_id=thread_id, records=records)
    if events:
        events.append(
            {
                "event": "tool.completed",
                "data": {
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "tool_name": tool_name,
                    "artifact_count": len(records),
                    "artifact_ids": [record.artifact_id for record in records],
                },
            }
        )
    return events


def map_deepagents_update(
    run_id: str,
    update: Any,
    *,
    thread_id: str | None = None,
    seen_tool_message_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Map a raw LangGraph/Deep Agents stream update into stable business events."""

    events: list[dict[str, Any]] = []
    if isinstance(update, dict):
        if "__interrupt__" in update:
            return [
                {
                    "event": "approval.required",
                    "data": {"run_id": run_id, "thread_id": thread_id, "interrupt": update},
                }
            ]
        if "todos" in update:
            return [
                {
                    "event": "plan.updated",
                    "data": {"run_id": run_id, "thread_id": thread_id, "todos": update["todos"]},
                }
            ]
        if "messages" in update and thread_id:
            records = _artifacts_register_from_messages(
                update["messages"],
                thread_id=thread_id,
                seen_message_ids=seen_tool_message_ids,
            )
            events.extend(
                artifact_created_events(run_id=run_id, thread_id=thread_id, records=records)
            )
            if update["messages"]:
                events.append(
                    {
                        "event": "message.delta",
                        "data": {
                            "run_id": run_id,
                            "thread_id": thread_id,
                            "delta": str(update["messages"]),
                        },
                    }
                )
            return events or [
                {
                    "event": "message.delta",
                    "data": {"run_id": run_id, "thread_id": thread_id, "delta": str(update)},
                }
            ]
    return [
        {
            "event": "message.delta",
            "data": {"run_id": run_id, "thread_id": thread_id, "delta": str(update)},
        }
    ]


async def stream_agent_sse(agent: Any, message: str, thread_id: str) -> AsyncIterator[str]:
    """Yield SSE frames for an agent run.

    Streaming mode selection (2026-06-03 review):

    * 当前主循环仍走 ``astream(stream_mode=["updates", "values"])``（与 v0.6
      以前的版本兼容），所以**不会**接收通过 ``get_stream_writer`` 写入的自
      定义数据 —— MinerU 解析的 ``mineru.local.request/response/poll`` 进度
      默认不会出现在这里。
    * 文档建议的写法是 ``stream_mode=["custom", "updates"]`` 配合节点内
      ``from langgraph.config import get_stream_writer`` 推送。
    * 未来 v0.6 typed-projection API ``agent.stream_events(version="v3")``
      与 ``get_stream_writer`` 不冲突；events 会包含 writer 数据作为
      ``on_custom_event``。
    * 工具层选择：当需要 MinerU 进度时调用 :func:`stream_agent_sse_with_progress`
      或自行 ``astream(..., stream_mode=["custom", "updates"])``。
    """

    run_id = f"run_{uuid.uuid4().hex[:12]}"
    seen_tool_message_ids: set[str] = set()
    registered_artifact_ids: list[str] = []
    yield sse_encode("run.started", {"run_id": run_id, "thread_id": thread_id})
    try:
        async for mode, chunk in agent.astream(
            {"messages": [{"role": "user", "content": message}]},
            config=_agent_build_thread_config(thread_id),
            stream_mode=["updates", "values"],
        ):
            if mode == "updates":
                for mapped in map_deepagents_update(
                    run_id,
                    chunk,
                    thread_id=thread_id,
                    seen_tool_message_ids=seen_tool_message_ids,
                ):
                    if mapped["event"] == "artifact.created":
                        artifact_id = mapped["data"].get("artifact_id")
                        if artifact_id:
                            registered_artifact_ids.append(str(artifact_id))
                    yield sse_encode(mapped["event"], mapped["data"])
            elif mode == "values":
                messages = chunk.get("messages", []) if isinstance(chunk, dict) else []
                for mapped in map_deepagents_update(
                    run_id,
                    {"messages": messages},
                    thread_id=thread_id,
                    seen_tool_message_ids=seen_tool_message_ids,
                ):
                    if mapped["event"] == "artifact.created":
                        artifact_id = mapped["data"].get("artifact_id")
                        if artifact_id and artifact_id not in registered_artifact_ids:
                            registered_artifact_ids.append(str(artifact_id))
                            yield sse_encode(mapped["event"], mapped["data"])
        yield sse_encode(
            "run.completed",
            {
                "run_id": run_id,
                "thread_id": thread_id,
                "artifact_ids": registered_artifact_ids,
            },
        )
    except Exception as exc:
        yield sse_encode(
            "run.failed",
            {
                "run_id": run_id,
                "thread_id": thread_id,
                "error": str(exc),
                "recoverable": True,
                "next_action": "检查配置或依赖。",
            },
        )


def build_resume_command(action: str, message: str | None = None) -> Command:
    """Build a LangGraph Command for HITL resume decisions."""

    if action not in {"approve", "reject", "edit"}:
        raise ValueError("action 仅支持 approve / reject / edit。")
    decision: dict[str, str] = {"type": action}
    if message:
        decision["message"] = message
    return Command(resume={"decisions": [decision]})


async def stream_agent_sse_with_progress(
    agent: Any,
    message: str,
    thread_id: str,
) -> AsyncIterator[str]:
    """Like :func:`stream_agent_sse` but also emits MinerU progress events.

    关键设计（2026-06-03 rev. 2）：

    * 使用 LangGraph ``version="v2"``：每个 chunk 是统一 ``StreamPart`` dict
      ``{"type": "...", "ns": (...), "data": ...}``，便于类型收敛与前端
      按 ``type`` 分发。
    * 启用 ``subgraphs=True``：Deep Agents 中 subagent（parser / extractor /
      reviewer）通过 ``task`` 工具被委派，**不启用 subgraphs 就收不到**
      它们内部 ``runtime.stream_writer`` 发出的进度事件。ns 为空代表主
      agent，``("tools:<call_id>",)`` 代表 subagent。
    * ``stream_mode=["custom", "updates"]``：同时拉取进度（custom）与状态
      增量（updates），前端用 `agent.progress`（含 ``ns`` / ``source``）+
      原有 `message.delta` / `artifact.created` 等事件。
    """

    run_id = f"run_{uuid.uuid4().hex[:12]}"
    seen_tool_message_ids: set[str] = set()
    registered_artifact_ids: list[str] = []
    yield sse_encode("run.started", {"run_id": run_id, "thread_id": thread_id})
    try:
        async for part in agent.astream(
            {"messages": [{"role": "user", "content": message}]},
            config=_agent_build_thread_config(thread_id),
            stream_mode=["custom", "updates"],
            subgraphs=True,
            version="v2",
        ):
            part_type = part.get("type") if isinstance(part, dict) else None
            if part_type == "custom":
                # 工具内通过 runtime.stream_writer 写入的任意 dict
                raw = part.get("data")
                ns = part.get("ns") or ()
                source = "subagent" if any(s.startswith("tools:") for s in ns) else "main"
                tool_call_id = next(
                    (s.split(":", 1)[1] for s in ns if s.startswith("tools:")),
                    None,
                )
                payload: dict[str, Any]
                if isinstance(raw, dict):
                    payload = dict(raw)
                else:
                    payload = {"data": raw}
                payload.update(
                    {
                        "run_id": run_id,
                        "thread_id": thread_id,
                        "source": source,
                        "ns": list(ns),
                    }
                )
                if tool_call_id:
                    payload["subagent_tool_call_id"] = tool_call_id
                yield sse_encode("agent.progress", payload)
                continue
            if part_type == "updates":
                chunk = part.get("data")
                for mapped in map_deepagents_update(
                    run_id,
                    chunk,
                    thread_id=thread_id,
                    seen_tool_message_ids=seen_tool_message_ids,
                ):
                    if mapped["event"] == "artifact.created":
                        artifact_id = mapped["data"].get("artifact_id")
                        if artifact_id:
                            registered_artifact_ids.append(str(artifact_id))
                    yield sse_encode(mapped["event"], mapped["data"])
                continue
            # 其它 part.type（values / checkpoints / tasks / debug / messages）
            # 暂不消费，避免噪音
        yield sse_encode(
            "run.completed",
            {
                "run_id": run_id,
                "thread_id": thread_id,
                "artifact_ids": registered_artifact_ids,
            },
        )
    except Exception as exc:
        yield sse_encode(
            "run.failed",
            {
                "run_id": run_id,
                "thread_id": thread_id,
                "error": str(exc),
                "recoverable": True,
                "next_action": "检查配置或依赖。",
            },
        )
