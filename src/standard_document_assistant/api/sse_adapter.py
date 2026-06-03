"""Map LangGraph SDK stream parts to business SSE events."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from standard_document_assistant.artifacts import (
    public_artifact_record,
    register_from_tool_result,
)


def coerce_stream_part(part: Any) -> tuple[str, Any, str | None]:
    """Return ``(event, data, id)`` from SDK stream part objects or dicts."""

    if isinstance(part, dict):
        return str(part.get("event") or part.get("type") or "message"), part.get("data"), part.get("id")
    return (
        str(getattr(part, "event", None) or getattr(part, "type", None) or "message"),
        getattr(part, "data", None),
        getattr(part, "id", None),
    )


def _iter_dicts(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _iter_dicts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_dicts(item)


def _message_payload(raw: dict[str, Any]) -> dict[str, Any] | None:
    if "kwargs" in raw and isinstance(raw["kwargs"], dict):
        return raw["kwargs"]
    if "lc_kwargs" in raw and isinstance(raw["lc_kwargs"], dict):
        return raw["lc_kwargs"]
    if any(key in raw for key in ("type", "name", "content", "id")):
        return raw
    return None


def _tool_name(message: dict[str, Any]) -> str:
    name = message.get("name") or message.get("tool_name") or ""
    if name:
        return str(name)
    additional = message.get("additional_kwargs")
    if isinstance(additional, dict):
        return str(additional.get("name") or additional.get("tool_name") or "")
    return ""


def _tool_content(message: dict[str, Any]) -> dict[str, Any] | None:
    content = message.get("content")
    if isinstance(content, dict):
        return content
    if isinstance(content, str) and content.strip():
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def register_serialized_tool_artifacts(
    data: Any,
    *,
    thread_id: str,
    seen_message_ids: set[str],
) -> list[dict[str, Any]]:
    """Register artifacts from serialized LangChain ToolMessage payloads."""

    events: list[dict[str, Any]] = []
    for raw in _iter_dicts(data):
        message = _message_payload(raw)
        if message is None:
            continue
        message_id = str(message.get("id") or "")
        if message_id and message_id in seen_message_ids:
            continue
        tool_name = _tool_name(message)
        tool_result = _tool_content(message)
        if not tool_name or tool_result is None:
            continue
        records = register_from_tool_result(
            thread_id=thread_id,
            tool_name=tool_name,
            tool_result=tool_result,
        )
        for record in records:
            events.append({"event": "artifact.created", "data": public_artifact_record(record)})
        if message_id:
            seen_message_ids.add(message_id)
    return events


def _has_key(data: Any, key: str) -> bool:
    return any(key in item for item in _iter_dicts(data))


def _first_value(data: Any, key: str) -> Any:
    for item in _iter_dicts(data):
        if key in item:
            return item[key]
    return None


def map_langgraph_part(
    part: Any,
    *,
    run_id: str,
    thread_id: str,
    seen_message_ids: set[str],
) -> list[dict[str, Any]]:
    """Map one upstream stream part into one or more business events."""

    upstream_event, data, upstream_id = coerce_stream_part(part)
    events = register_serialized_tool_artifacts(
        data,
        thread_id=thread_id,
        seen_message_ids=seen_message_ids,
    )

    if upstream_event == "custom":
        payload = dict(data) if isinstance(data, dict) else {"data": data}
        payload.update({"run_id": run_id, "thread_id": thread_id})
        events.append({"event": "agent.progress", "data": payload})
        return events

    if _has_key(data, "__interrupt__"):
        events.append(
            {
                "event": "approval.required",
                "data": {
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "interrupt": _first_value(data, "__interrupt__"),
                },
            }
        )
        return events

    if _has_key(data, "todos"):
        events.append(
            {
                "event": "plan.updated",
                "data": {
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "todos": _first_value(data, "todos"),
                },
            }
        )
        return events

    if upstream_event in {"messages", "messages/partial", "messages-tuple"}:
        events.append(
            {
                "event": "message.delta",
                "data": {
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "delta": data,
                    "upstream_event": upstream_event,
                    "upstream_id": upstream_id,
                },
            }
        )
        return events

    if upstream_event in {"updates", "values"}:
        events.append(
            {
                "event": "message.delta",
                "data": {
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "delta": data,
                    "upstream_event": upstream_event,
                    "upstream_id": upstream_id,
                },
            }
        )
        return events

    if upstream_event in {"error", "errors"}:
        events.append(
            {
                "event": "run.failed",
                "data": {
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "error": data,
                    "recoverable": True,
                },
            }
        )
        return events

    events.append(
        {
            "event": "langgraph.event",
            "data": {
                "run_id": run_id,
                "thread_id": thread_id,
                "upstream_event": upstream_event,
                "upstream_id": upstream_id,
                "data": data,
            },
        }
    )
    return events
