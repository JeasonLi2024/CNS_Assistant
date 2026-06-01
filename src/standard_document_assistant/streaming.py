"""SSE event helpers for Deep Agents runs."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

from langgraph.types import Command

from standard_document_assistant.agent import build_thread_config
from standard_document_assistant.artifacts import (
    public_artifact_record,
    register_artifacts_from_messages,
    register_from_tool_result,
)


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
        payload = public_artifact_record(record)
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
    records = register_from_tool_result(
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
            records = register_artifacts_from_messages(
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
    """Yield SSE frames for an agent run."""

    run_id = f"run_{uuid.uuid4().hex[:12]}"
    seen_tool_message_ids: set[str] = set()
    registered_artifact_ids: list[str] = []
    yield sse_encode("run.started", {"run_id": run_id, "thread_id": thread_id})
    try:
        async for mode, chunk in agent.astream(
            {"messages": [{"role": "user", "content": message}]},
            config=build_thread_config(thread_id),
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

    if action not in {"approve", "reject"}:
        raise ValueError("action 仅支持 approve 或 reject。")
    decision: dict[str, str] = {"type": action}
    if message:
        decision["message"] = message
    return Command(resume={"decisions": [decision]})
