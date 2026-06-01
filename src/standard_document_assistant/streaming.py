"""SSE event helpers for Deep Agents runs."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from langgraph.types import Command

from standard_document_assistant.agent import build_thread_config


def sse_encode(event: str, data: dict[str, Any]) -> str:
    """Encode one Server-Sent Event frame."""

    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def map_deepagents_update(run_id: str, update: Any) -> dict[str, Any]:
    """Map a raw LangGraph/Deep Agents stream update into a stable business event."""

    if isinstance(update, dict):
        if "__interrupt__" in update:
            return {"event": "approval.required", "data": {"run_id": run_id, "interrupt": update}}
        if "todos" in update:
            return {"event": "plan.updated", "data": {"run_id": run_id, "todos": update["todos"]}}
        if "messages" in update:
            return {"event": "message.delta", "data": {"run_id": run_id, "delta": str(update["messages"])}}
    return {"event": "message.delta", "data": {"run_id": run_id, "delta": str(update)}}


async def stream_agent_sse(agent: Any, message: str, thread_id: str) -> AsyncIterator[str]:
    """Yield SSE frames for an agent run."""

    run_id = f"run_{uuid.uuid4().hex[:12]}"
    yield sse_encode("run.started", {"run_id": run_id, "thread_id": thread_id})
    try:
        async for update in agent.astream(
            {"messages": [{"role": "user", "content": message}]},
            config=build_thread_config(thread_id),
            stream_mode="updates",
        ):
            mapped = map_deepagents_update(run_id, update)
            yield sse_encode(mapped["event"], mapped["data"])
        yield sse_encode("run.completed", {"run_id": run_id})
    except Exception as exc:
        yield sse_encode(
            "run.failed",
            {"run_id": run_id, "error": str(exc), "recoverable": True, "next_action": "检查配置或依赖。"},
        )


def build_resume_command(action: str, message: str | None = None) -> Command:
    """Build a LangGraph Command for HITL resume decisions."""

    if action not in {"approve", "reject"}:
        raise ValueError("action 仅支持 approve 或 reject。")
    decision: dict[str, str] = {"type": action}
    if message:
        decision["message"] = message
    return Command(resume={"decisions": [decision]})

