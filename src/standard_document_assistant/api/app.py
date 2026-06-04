"""FastAPI BFF for local standard document assistant testing."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from langgraph_sdk.schema import Command

from standard_document_assistant.api.langgraph_client import get_langgraph_client
from standard_document_assistant.api.models import (
    CreateThreadRequest,
    DirectStandardReviewRequest,
    ResumeRunRequest,
    RunStreamRequest,
    StandardReviewRequest,
)
from standard_document_assistant.api.settings import get_settings
from standard_document_assistant.api.sse_adapter import map_langgraph_part
from standard_document_assistant.artifacts import (
    list_thread_artifacts,
    public_artifact_record,
    register_from_tool_result,
    resolve_thread_artifact_path,
)
from standard_document_assistant.pathing import utc_now_iso
from standard_document_assistant.streaming import sse_encode
from standard_document_assistant.tools.review import _build_initial_state, _public_result
from standard_document_assistant.tracing import STANDARD_REVIEW_TOOL_NAME
from standard_document_assistant.uploads import save_uploaded_file


settings = get_settings()
os.environ.setdefault("STANDARD_DOC_ARTIFACT_API_BASE", settings.artifact_api_base)

app = FastAPI(
    title="Standard Document Assistant API",
    version="0.1.0",
    description="Local FastAPI BFF for LangGraph Server hosted Deep Agents.",
)


def _assistant_id(value: str | None) -> str:
    return value or get_settings().assistant_id


def _stream_modes(value: list[str] | None) -> list[str]:
    return list(value or get_settings().default_stream_modes)


def _input_from_message(message: str | None, raw_input: dict | None) -> dict:
    if raw_input is not None:
        return raw_input
    if not message or not message.strip():
        raise HTTPException(status_code=400, detail="message 和 input 至少提供一个。")
    return {"messages": [{"role": "user", "content": message}]}


def _review_message(payload: StandardReviewRequest) -> str:
    extra = f"\n\n补充要求：{payload.instruction.strip()}" if payload.instruction else ""
    return (
        "请对以下标准文档执行标准审核，按解析 -> 信息抽取 -> 审核 -> 报告生成流程处理。\n\n"
        f"文件路径：{payload.file_path}\n"
        "请返回关键发现、风险等级、审核报告和可下载产物路径。"
        f"{extra}"
    )


def _build_direct_review_state(payload: DirectStandardReviewRequest) -> dict[str, Any]:
    options = payload.review_options
    mode = options.mode
    content_path: str | None = None
    source_path: str | None = None
    format_only = False
    partial_mode = options.partial_mode
    target_scopes = options.target_scopes
    line_start = options.line_start
    line_end = options.line_end

    if mode == "format_only":
        source_path = payload.source_path or payload.file_path
        format_only = True
        partial_mode = partial_mode or "format_only"
        target_scopes = target_scopes or ["format"]
    else:
        content_path = payload.file_path
        if mode == "content_and_format":
            source_path = payload.source_path
        if mode == "full_document_content":
            partial_mode = "full_document"
        elif mode == "scoped_content":
            partial_mode = partial_mode or "sectional"
            if not target_scopes:
                raise HTTPException(status_code=400, detail="scoped_content 需要提供 target_scopes。")
        elif mode == "line_range_content":
            partial_mode = partial_mode or "sectional"
            if line_start is None and line_end is None:
                raise HTTPException(
                    status_code=400,
                    detail="line_range_content 需要提供 line_start 或 line_end。",
                )
        else:
            partial_mode = partial_mode or "sectional"

    state = _build_initial_state(
        content_path=content_path,
        source_path=source_path,
        manifest_path=payload.manifest_path,
        target_scopes=target_scopes,
        line_start=line_start,
        line_end=line_end,
        top_k=options.top_k,
        format_only=format_only,
        output_subdir=payload.output_subdir,
        trace_id=payload.trace_id,
        force_rebuild_index=options.force_rebuild_index,
        partial_mode=partial_mode,
    )
    if options.disable_widen:
        state["max_review_rounds"] = 0
    elif options.max_review_rounds is not None:
        state["max_review_rounds"] = options.max_review_rounds
    state["api_review_options"] = options.model_dump()
    if payload.instruction:
        state["api_instruction"] = payload.instruction
    return state


def _safe_load_virtual_file(virtual_path: str) -> str:
    if not virtual_path:
        return ""
    from standard_document_assistant.pathing import virtual_to_host_path

    host = virtual_to_host_path(virtual_path)
    if not host.exists() or not host.is_file():
        return ""
    return host.read_text(encoding="utf-8", errors="ignore")


def _safe_load_virtual_json(virtual_path: str) -> dict[str, Any]:
    if not virtual_path:
        return {}
    import json

    text = _safe_load_virtual_file(virtual_path)
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _passed_from_public_result(public: dict[str, Any], result_json: dict[str, Any]) -> bool:
    if public.get("status") != "success":
        return False
    summary = public.get("summary") or {}
    failed = int(summary.get("failed") or 0)
    errors = int(summary.get("errors") or 0)
    critical = int((summary.get("by_severity") or {}).get("critical") or 0)
    if failed or errors or critical:
        return False
    issues = result_json.get("issues") or []
    if isinstance(issues, list):
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            if issue.get("status") == "fail":
                return False
            if issue.get("severity") == "critical":
                return False
    return True


def _artifact_map(records: list[Any]) -> dict[str, dict[str, Any]]:
    mapped: dict[str, dict[str, Any]] = {}
    for record in records:
        mapped[record.artifact_type] = public_artifact_record(record)
    return mapped


def _direct_review_response(
    *,
    thread_id: str,
    payload: DirectStandardReviewRequest,
    state_result: dict[str, Any],
) -> dict[str, Any]:
    public = _public_result(state_result)
    records = []
    if public.get("status") == "success":
        records = register_from_tool_result(
            thread_id=thread_id,
            tool_name=STANDARD_REVIEW_TOOL_NAME,
            tool_result=public,
        )
    artifacts = public.get("artifacts") or {}
    report_path = str(artifacts.get("report") or "")
    result_path = str(artifacts.get("result") or "")
    report_markdown = _safe_load_virtual_file(report_path) if payload.return_report_content else ""
    result_json = _safe_load_virtual_json(result_path) if payload.return_result_json else {}
    return {
        "status": "completed" if public.get("status") == "success" else "failed",
        "thread_id": thread_id,
        "passed": _passed_from_public_result(public, result_json),
        "review": public,
        "review_report_markdown": report_markdown,
        "review_result": result_json,
        "artifacts": _artifact_map(records),
        "review_options": payload.review_options.model_dump(),
    }


@app.get("/health")
async def health() -> dict[str, object]:
    current = get_settings()
    return {
        "status": "ok",
        "app": "standard-document-assistant-api",
        "langgraph_api_url": current.langgraph_api_url,
        "assistant_id": current.assistant_id,
        "artifact_api_base": current.artifact_api_base,
        "created_at": utc_now_iso(),
    }


@app.post("/api/threads")
async def create_thread(payload: CreateThreadRequest | None = None) -> dict:
    body = payload or CreateThreadRequest()
    client = get_langgraph_client()
    try:
        thread = await client.threads.create(
            thread_id=body.thread_id,
            metadata=body.metadata,
            if_exists="do_nothing" if body.thread_id else None,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"创建 LangGraph thread 失败：{exc}") from exc
    return dict(thread)


@app.post("/api/threads/{thread_id}/uploads")
async def upload_file(thread_id: str, file: UploadFile = File(...)) -> dict:
    content = await file.read()
    try:
        record = save_uploaded_file(
            original_filename=file.filename or "upload",
            content=content,
            thread_id=thread_id,
            content_type=file.content_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return record.model_dump()


@app.post("/api/threads/{thread_id}/runs/stream")
async def stream_run(thread_id: str, payload: RunStreamRequest) -> StreamingResponse:
    input_payload = _input_from_message(payload.message, payload.input)
    return StreamingResponse(
        _stream_run_events(
            thread_id=thread_id,
            assistant_id=_assistant_id(payload.assistant_id),
            input_payload=input_payload,
            stream_modes=_stream_modes(payload.stream_modes),
            stream_subgraphs=(
                get_settings().stream_subgraphs
                if payload.stream_subgraphs is None
                else payload.stream_subgraphs
            ),
            metadata=payload.metadata,
            context=payload.context,
        ),
        media_type="text/event-stream",
    )


@app.post("/api/threads/{thread_id}/standard-review/stream")
async def stream_standard_review(
    thread_id: str,
    payload: StandardReviewRequest,
) -> StreamingResponse:
    return StreamingResponse(
        _stream_run_events(
            thread_id=thread_id,
            assistant_id=_assistant_id(payload.assistant_id),
            input_payload={"messages": [{"role": "user", "content": _review_message(payload)}]},
            stream_modes=list(get_settings().default_stream_modes),
            stream_subgraphs=get_settings().stream_subgraphs,
            metadata={"task_type": "standard_review", "source_virtual_path": payload.file_path},
            context=None,
        ),
        media_type="text/event-stream",
    )


@app.post("/api/review-jobs/standard-review")
async def direct_standard_review(payload: DirectStandardReviewRequest) -> dict[str, Any]:
    """Run the standard_review graph directly for machine workflows."""

    thread_id = payload.thread_id or str(uuid.uuid4())
    state = _build_direct_review_state(payload)
    client = get_langgraph_client()
    try:
        result = await _run_direct_review_nonstream(client, thread_id=thread_id, state=state)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"标准审核执行失败：{exc}") from exc
    if not isinstance(result, dict):
        raise HTTPException(status_code=502, detail="标准审核返回值不是对象。")
    return _direct_review_response(thread_id=thread_id, payload=payload, state_result=result)


async def _run_direct_review_nonstream(
    client,
    *,
    thread_id: str,
    state: dict[str, Any],
) -> dict[str, Any]:
    """Run standard_review and return the final state.

    Some local LangGraph dev versions expose streaming runs but return 404 for
    the SDK's convenience ``/runs/wait`` endpoint. Keep ``wait`` as the primary
    path, then fall back to collecting the last ``values`` event from stream.
    """

    try:
        result = await client.runs.wait(
            thread_id,
            "standard_review",
            input=state,
            raise_error=True,
            if_not_exists="create",
        )
        if isinstance(result, dict):
            return result
    except Exception as exc:
        if not _is_runs_wait_unavailable(exc):
            raise

    latest_state: dict[str, Any] = {}
    async for part in client.runs.stream(
        thread_id,
        "standard_review",
        input=state,
        stream_mode=["values"],
        stream_subgraphs=True,
        if_not_exists="create",
    ):
        event = getattr(part, "event", None)
        data = getattr(part, "data", None)
        if isinstance(part, dict):
            event = part.get("event")
            data = part.get("data")
        if event == "values" and isinstance(data, dict):
            latest_state = data
    if not latest_state:
        raise RuntimeError("LangGraph runs.wait 不可用，stream fallback 也没有返回最终 state。")
    return latest_state


def _is_runs_wait_unavailable(exc: Exception) -> bool:
    text = str(exc)
    return "404" in text and "/runs/wait" in text


@app.post("/api/review-jobs/standard-review/stream")
async def direct_standard_review_stream(payload: DirectStandardReviewRequest) -> StreamingResponse:
    """Run the standard_review graph directly and stream graph events."""

    thread_id = payload.thread_id or str(uuid.uuid4())
    state = _build_direct_review_state(payload)
    return StreamingResponse(
        _stream_direct_review_events(thread_id=thread_id, payload=payload, state=state),
        media_type="text/event-stream",
    )


async def _stream_direct_review_events(
    *,
    thread_id: str,
    payload: DirectStandardReviewRequest,
    state: dict[str, Any],
) -> AsyncIterator[str]:
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    client = get_langgraph_client()
    latest_state: dict[str, Any] = {}
    yield sse_encode(
        "run.started",
        {
            "run_id": run_id,
            "thread_id": thread_id,
            "assistant_id": "standard_review",
            "review_options": payload.review_options.model_dump(),
        },
    )
    try:
        async for part in client.runs.stream(
            thread_id,
            "standard_review",
            input=state,
            stream_mode=["custom", "updates", "values"],
            stream_subgraphs=True,
        ):
            event = getattr(part, "event", None)
            data = getattr(part, "data", None)
            if isinstance(part, dict):
                event = part.get("event")
                data = part.get("data")
            if event == "custom":
                custom = dict(data) if isinstance(data, dict) else {"data": data}
                custom.update({"run_id": run_id, "thread_id": thread_id})
                yield sse_encode("agent.progress", custom)
            elif event == "values" and isinstance(data, dict):
                latest_state = data
                yield sse_encode(
                    "review.snapshot",
                    {
                        "run_id": run_id,
                        "thread_id": thread_id,
                        "status": data.get("status", ""),
                        "job_id": data.get("job_id", ""),
                    },
                )
            elif event == "updates":
                yield sse_encode(
                    "review.update",
                    {"run_id": run_id, "thread_id": thread_id, "data": data},
                )
        response = _direct_review_response(
            thread_id=thread_id,
            payload=payload,
            state_result=latest_state or state,
        )
        yield sse_encode("review.completed", response)
        yield sse_encode(
            "run.completed",
            {
                "run_id": run_id,
                "thread_id": thread_id,
                "passed": response["passed"],
                "artifact_types": list(response["artifacts"].keys()),
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
            },
        )


async def _stream_run_events(
    *,
    thread_id: str,
    assistant_id: str,
    input_payload: dict,
    stream_modes: list[str],
    stream_subgraphs: bool,
    metadata: dict | None,
    context: dict | None,
) -> AsyncIterator[str]:
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    seen_message_ids: set[str] = set()
    artifact_ids: list[str] = []
    yield sse_encode(
        "run.started",
        {
            "run_id": run_id,
            "thread_id": thread_id,
            "assistant_id": assistant_id,
            "stream_modes": stream_modes,
        },
    )
    client = get_langgraph_client()
    try:
        async for part in client.runs.stream(
            thread_id,
            assistant_id,
            input=input_payload,
            stream_mode=stream_modes,
            stream_subgraphs=stream_subgraphs,
            metadata=metadata,
            context=context,
        ):
            for mapped in map_langgraph_part(
                part,
                run_id=run_id,
                thread_id=thread_id,
                seen_message_ids=seen_message_ids,
            ):
                if mapped["event"] == "artifact.created":
                    artifact_id = mapped["data"].get("artifact_id")
                    if artifact_id:
                        artifact_ids.append(str(artifact_id))
                yield sse_encode(mapped["event"], mapped["data"])
        yield sse_encode(
            "run.completed",
            {
                "run_id": run_id,
                "thread_id": thread_id,
                "artifact_ids": artifact_ids,
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
                "next_action": "确认 langgraph dev 已启动，并检查模型、MinerU 与文件路径配置。",
            },
        )


@app.post("/api/threads/{thread_id}/runs/resume")
async def resume_run(thread_id: str, payload: ResumeRunRequest) -> dict:
    client = get_langgraph_client()
    decision: dict[str, str] = {"type": payload.action}
    if payload.message:
        decision["message"] = payload.message
    command = Command(resume={"decisions": [decision]})
    try:
        result = await client.runs.wait(
            thread_id,
            _assistant_id(payload.assistant_id),
            command=command,
            metadata=payload.metadata,
            context=payload.context,
            raise_error=False,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"恢复 LangGraph run 失败：{exc}") from exc
    return {"thread_id": thread_id, "result": result}


@app.get("/api/threads/{thread_id}/artifacts")
async def artifacts(thread_id: str) -> dict[str, object]:
    records = list_thread_artifacts(thread_id)
    return {
        "thread_id": thread_id,
        "artifacts": [public_artifact_record(record) for record in records],
    }


@app.get("/api/threads/{thread_id}/artifacts/{artifact_id}/download")
async def download_artifact(thread_id: str, artifact_id: str) -> FileResponse:
    try:
        path = resolve_thread_artifact_path(thread_id, artifact_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    record = next(
        (item for item in list_thread_artifacts(thread_id) if item.artifact_id == artifact_id),
        None,
    )
    media_type = record.content_type if record is not None else "application/octet-stream"
    filename = record.stored_filename if record is not None else path.name
    return FileResponse(path, media_type=media_type, filename=filename)
