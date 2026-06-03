"""Document parsing tool backed by MinerU.

Design notes (2026-06-03, rev. 2)
---------------------------------
1. **同步 / 异步双实现**：保留 ``_parse_file_with_mineru_sync`` 作为同步实现，
   新增 ``_parse_file_with_mineru_async`` 用 ``asyncio.to_thread`` 包裹，
   通过 ``StructuredTool.from_function(func=..., coroutine=...)`` 同时暴露。
2. **进度推送（v2 写法）**：
   - 工具入参 ``runtime: ToolRuntime | None = None``（LangChain 1.x 推荐的
     新写法，**不再**使用 ``InjectedToolArg``）。
   - 进度通过 ``runtime.stream_writer`` 推送，避免 ``get_stream_writer()``
     在 Python<3.11 async 上下文下失效的问题（官方文档原话）。
   - 同步 / 异步版本都接收外层传入的 ``stream_writer``（async wrapper 在
     事件循环线程内捕获，确保 thread-safe），保证事件始终写到正确的 writer。
3. **断点续跑 / 缓存**：按设计文档 §3.3 item 5 取消；
   保留 ``skip_if_zip_exists`` 作为本地 ZIP **性能缓存**。
4. **HITL 决策粒度**：由 ``agent.py:build_subagents.parser_spec`` 决定，
   本工具不内置 HITL 触发。
5. **错误分类**：网络 / 协议错误走 ``MinerURequestError``，配置 / 参数错误
   走 ``MinerUConfigError``，由 ``client.py`` 抛出。
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool

from standard_document_assistant.config import load_config
from standard_document_assistant.constants import SAMPLES_DIR, UPLOADS_DIR
from standard_document_assistant.integrations.mineru.client import request_parse_file
from standard_document_assistant.integrations.mineru.zip_parser import parse_result_zip
from standard_document_assistant.pathing import (
    allocate_unique_path,
    host_to_virtual_path,
    mineru_output_root,
    resolve_workspace_read_path,
    safe_name,
    utc_now_iso,
    write_json,
)
from standard_document_assistant.schemas import ArtifactManifest, ArtifactRef, MinerUParseResult
from standard_document_assistant.tracing import PARSE_FILE_WITH_MINERU_TOOL_NAME


logger = logging.getLogger(__name__)


SUPPORTED_MINERU_SUFFIXES = {".pdf", ".docx"}


def _trace_metadata(*, runtime: ToolRuntime | None, file_path: str) -> dict[str, Any]:
    extra: dict[str, Any] = {"source_virtual_path": file_path}
    if runtime is not None:
        configurable = (runtime.config or {}).get("configurable") or {}
        if isinstance(configurable, dict) and configurable.get("thread_id"):
            extra["thread_id"] = configurable["thread_id"]
    return extra


def _get_stream_writer(runtime: ToolRuntime | None) -> Any | None:
    """Return ``runtime.stream_writer`` if the tool is invoked in a graph.

    重要：tool 推荐使用 ``ToolRuntime.stream_writer``（LangChain 1.x），
    而不是 ``langgraph.config.get_stream_writer()``。后者在 async + Python<3.11
    下不可用，前者由 runtime 显式持有，对 sync / async / 子线程都安全。
    """

    if runtime is None:
        return None
    return getattr(runtime, "stream_writer", None)


def _emit_event(
    event: dict[str, Any],
    *,
    writer: Any | None,
) -> None:
    """把进度事件推给 LangGraph stream writer。

    ``writer`` 由调用方（sync / async 包装）显式传入：

    - 同步实现：直接传 ``runtime.stream_writer``；
    - 异步实现：外层 wrapper 在事件循环线程内捕获 ``runtime.stream_writer``，
      传入 worker 线程，**避免** worker 线程重新解析 contextvar。
    """

    if writer is None:
        return
    try:
        writer(event)
    except (RuntimeError, KeyError, AttributeError):
        # 图外调用 / runtime 未注入 stream_writer；不抛错避免中断解析
        return
    except (TypeError, ValueError):
        # 序列化 / payload 错误 —— 真正需要被记录的可观测性事件
        logger.warning("MinerU 进度事件推送失败：payload=%r", event, exc_info=True)


def _parse_file_with_mineru_sync(
    file_path: str,
    *,
    return_images: bool | None = None,
    save_zip_archive: bool | None = None,
    save_middle_json: bool | None = None,
    save_content_list: bool | None = None,
    skip_if_zip_exists: bool | None = None,
    output_subdir: str | None = None,
    runtime: ToolRuntime | None = None,
) -> dict[str, Any]:
    """Parse an uploaded PDF or Word document into Markdown and artifacts using MinerU."""

    started = time.perf_counter()
    writer = _get_stream_writer(runtime)
    config = load_config()
    mineru_config = config.mineru
    return_images = mineru_config.return_images if return_images is None else return_images
    save_zip_archive = mineru_config.save_zip_archive if save_zip_archive is None else save_zip_archive
    save_middle_json = mineru_config.save_middle_json if save_middle_json is None else save_middle_json
    save_content_list = (
        mineru_config.save_content_list if save_content_list is None else save_content_list
    )
    skip_if_zip_exists = (
        mineru_config.skip_if_zip_exists if skip_if_zip_exists is None else skip_if_zip_exists
    )
    output_root = mineru_output_root(output_subdir or mineru_config.output_subdir)
    source_path, source_virtual = resolve_workspace_read_path(
        file_path,
        allowed_roots=[UPLOADS_DIR, SAMPLES_DIR],
        suffixes=SUPPORTED_MINERU_SUFFIXES,
    )
    max_bytes = mineru_config.max_pdf_size_mb * 1024 * 1024
    if source_path.stat().st_size > max_bytes:
        raise ValueError(f"文件超过 MinerU 大小限制：{mineru_config.max_pdf_size_mb}MB")

    zip_dir = output_root / "zip"
    zip_path = zip_dir / f"{safe_name(source_path.stem)}.zip"
    zip_cache_hit = False
    if skip_if_zip_exists and zip_path.exists():
        zip_bytes = zip_path.read_bytes()
        zip_cache_hit = True
    else:

        def _on_event(event: dict[str, Any]) -> None:
            # 透传到 LangGraph stream writer，前端通过 stream_mode="custom" 消费
            _emit_event(event, writer=writer)

        zip_bytes = request_parse_file(
            source_path,
            mineru_config,
            return_images=return_images,
            on_event=_on_event,
        )
        if save_zip_archive:
            zip_dir.mkdir(parents=True, exist_ok=True)
            zip_path.write_bytes(zip_bytes)

    parsed = parse_result_zip(
        zip_bytes=zip_bytes,
        source_stem=source_path.stem,
        output_root=output_root,
        return_images=return_images,
        save_middle_json=save_middle_json,
        save_content_list=save_content_list,
    )
    artifacts = [ArtifactRef.model_validate(item) for item in parsed["artifacts"]]
    if save_zip_archive and zip_path.exists():
        zip_ref = ArtifactRef(
            type="zip",
            virtual_path=host_to_virtual_path(zip_path),
            description="MinerU 原始 ZIP",
        )
        artifacts.append(zip_ref)
    primary = next((item for item in artifacts if item.type == "markdown"), None)
    manifest_path = allocate_unique_path(
        output_root / "manifests", safe_name(Path(parsed["md_path"]).stem) + "_parse_manifest", ".json"
    )
    parse_warnings = list(parsed.get("warnings") or [])
    manifest = ArtifactManifest(
        tool=PARSE_FILE_WITH_MINERU_TOOL_NAME,
        status="ok",
        source_virtual_path=source_virtual,
        primary_artifact=primary,
        artifacts=artifacts,
        warnings=parse_warnings,
        error="",
        created_at=utc_now_iso(),
    )
    manifest_payload = manifest.model_dump()
    manifest_payload["cover_metadata"] = parsed["cover_metadata"]
    manifest_payload["trace"] = _trace_metadata(runtime=runtime, file_path=source_virtual)
    write_json(manifest_path, manifest_payload)
    result = MinerUParseResult(
        status="ok",
        source_virtual_path=source_virtual,
        virtual_md_path=primary.virtual_path if primary else "",
        virtual_manifest_path=host_to_virtual_path(manifest_path),
        virtual_zip_path=host_to_virtual_path(zip_path) if zip_path.exists() else "",
        virtual_image_root=(
            host_to_virtual_path(parsed["image_root"]) + "/" if parsed.get("image_root") else ""
        ),
        cover_metadata=parsed["cover_metadata"],
        warnings=parse_warnings,
        duration_ms=int((time.perf_counter() - started) * 1000),
        zip_cache_hit=zip_cache_hit,
    )
    # 收尾进度事件，告知前端完成
    _emit_event(
        {
            "type": "mineru.parse.completed",
            "file": source_path.name,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "zip_cache_hit": zip_cache_hit,
            "virtual_md_path": result.virtual_md_path,
        },
        writer=writer,
    )
    return result.model_dump()


async def _parse_file_with_mineru_async(
    file_path: str,
    *,
    return_images: bool | None = None,
    save_zip_archive: bool | None = None,
    save_middle_json: bool | None = None,
    save_content_list: bool | None = None,
    skip_if_zip_exists: bool | None = None,
    output_subdir: str | None = None,
    runtime: ToolRuntime | None = None,
) -> dict[str, Any]:
    """Async wrapper: off-load the blocking ``_parse_file_with_mineru_sync`` to a thread.

    设计要点：
    * **local mode** – a single blocking ``requests.post`` is moved to a worker
      thread via ``asyncio.to_thread``.
    * **precise mode** – the polling loop contains ``time.sleep``; because the
      entire body runs inside a thread, the event loop stays free to process
      other coroutines / stream deltas.
    * ``runtime.stream_writer`` **在事件循环线程内**捕获后通过闭包传入 worker
      线程，避免在 worker 中重新解析 contextvar / runtime。
    """

    writer = _get_stream_writer(runtime)
    return await asyncio.to_thread(
        _parse_file_with_mineru_sync,
        file_path,
        return_images=return_images,
        save_zip_archive=save_zip_archive,
        save_middle_json=save_middle_json,
        save_content_list=save_content_list,
        skip_if_zip_exists=skip_if_zip_exists,
        output_subdir=output_subdir,
        runtime=runtime,  # 保留用于 _trace_metadata；writer 走闭包
    )


# 共享参数 docstring（同步实现提供；Tool 会自动识别）
parse_file_with_mineru = StructuredTool.from_function(
    func=_parse_file_with_mineru_sync,
    coroutine=_parse_file_with_mineru_async,
    name=PARSE_FILE_WITH_MINERU_TOOL_NAME,
    description=(
        "Parse an uploaded PDF or Word standard document via MinerU into Markdown, "
        "images, JSON sidecars, and a manifest under /workspace/output/mineru. "
        "Use ONLY for files at /workspace/input/uploads/** or "
        "/workspace/input/samples/** with suffix .pdf or .docx. "
        "This is the main entry point for the parser subagent — call it as the "
        "first step before any metadata extraction or review. "
        "The tool picks the right backend (local self-hosted / precise cloud) "
        "from config; precise mode may take minutes and emits progress events "
        "via the custom stream mode."
    ),
)


def parse_pdf_with_mineru(file_path: str, **kwargs: Any) -> dict[str, Any]:
    """Backward-compatible wrapper for callers that still use the old Python function."""

    return _parse_file_with_mineru_sync(file_path, **kwargs)
