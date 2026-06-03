"""HTTP client for MinerU.

Design notes (2026-06-03, rev. 2)
---------------------------------
1. **RetryPolicy**: 网络层只做同步重试（指数退避 + jitter）。Deep Agents 工具层
   的 ``ToolNode(..., handle_tool_errors=True)`` 会把 ``MinerURequestError`` /
   业务错误转成 ``ToolMessage`` 让 LLM 自决，节点级
   ``retry_policy=RetryPolicy(...)`` 只对 graph 节点生效，不直接作用在工具内部。
   因此对 ``_request_local_parse_file`` 这类纯网络调用，我们在客户端内显式
   做 ``max_attempts=3`` 的退避重试，**只对** ``MinerURequestError`` /
   ``requests.exceptions.*`` 重试，配置 / 参数错误 (``MinerUConfigError``)
   **不**进入重试循环。
2. **Progress callback**: 通过可选 ``on_event`` 回调把阶段信息透出
   （``mineru.parse.local.request`` / ``mineru.parse.local.response`` /
   ``mineru.parse.precise.poll`` 等），由工具层在 ``runtime.stream_writer``
   里推给前端。
3. **Async**: 客户端保持纯同步；工具层用 ``asyncio.to_thread`` 包裹即可
   （polling 里的 ``time.sleep`` 在线程里阻塞不会卡事件循环）。stream_writer
   闭包在事件循环线程内捕获，避免 worker 线程重新解析 contextvar。
4. **Batch**: ``precise`` API 本身支持批量（``/api/v4/file-urls/batch`` 接受
   ``files: [...]``）；``local`` 不支持单接口多文件，需要客户端并发。
   见 :func:`request_parse_files_parallel`。
"""

from __future__ import annotations

import logging
import mimetypes
import random
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from standard_document_assistant.config import MinerUConfig
from standard_document_assistant.integrations.mineru.config import build_request_data


logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 异常分类（设计评审 C3）
# ─────────────────────────────────────────────────────────────────────────────


class MinerUError(Exception):
    """Base class for all MinerU client errors.

    区分两层：
    * :class:`MinerUConfigError` — 配置 / 参数 / 输入错误（不重试）
    * :class:`MinerURequestError` — 网络 / 协议 / 后端错误（可重试）
    """

    def __init__(self, message: str, **context: Any) -> None:
        super().__init__(message)
        self.context = context


class MinerUConfigError(MinerUError):
    """MinerU 配置缺失 / 文件类型不支持 / 后端模式不识别等"不该重试"的错误。"""


class MinerURequestError(MinerUError):
    """MinerU 网络 / 协议 / 后端响应错误（可重试；recoverable）。"""


# 阶段事件类型常量（命名空间：mineru.parse.{local|precise}.*，与 L3 对齐）
EVENT_LOCAL_REQUEST = "mineru.parse.local.request"
EVENT_LOCAL_RESPONSE = "mineru.parse.local.response"
EVENT_LOCAL_RETRY = "mineru.parse.local.retry"
EVENT_PRECISE_APPLY = "mineru.parse.precise.apply"
EVENT_PRECISE_UPLOAD = "mineru.parse.precise.upload"
EVENT_PRECISE_POLL = "mineru.parse.precise.poll"
EVENT_PRECISE_DOWNLOAD = "mineru.parse.precise.download"

# 工具层可覆写的默认重试配置（与 langgraph RetryPolicy 语义对齐）
DEFAULT_RETRY = {
    "max_attempts": 3,
    "initial_interval": 2.0,
    "max_interval": 10.0,
    "backoff_factor": 2.0,
    "jitter": True,
}

ProgressCallback = Callable[[dict[str, Any]], None]


def _noop_event(_: dict[str, Any]) -> None:
    return None


def _content_type_for_file(file_path: Path) -> str:
    if file_path.suffix.lower() == ".docx":
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if file_path.suffix.lower() == ".pdf":
        return "application/pdf"
    return mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"


def _compute_backoff(
    *,
    attempt: int,
    initial_interval: float,
    max_interval: float,
    backoff_factor: float,
    jitter: bool,
) -> float:
    """Compute sleep duration for one retry attempt (matches langgraph RetryPolicy)."""

    raw = min(initial_interval * (backoff_factor ** max(attempt - 1, 0)), max_interval)
    if jitter:
        # full jitter: 均匀采样 [0, raw]，避免雷鸣群
        return random.uniform(0.0, raw)
    return raw


def _retry_with_policy(
    func: Callable[[], Any],
    *,
    description: str,
    on_event: ProgressCallback | None = None,
    retry_on: Sequence[type[BaseException]] | None = None,
    config: dict[str, Any] | None = None,
) -> Any:
    """Run ``func`` with the same backoff/jitter semantics as ``RetryPolicy``.

    Args:
        func: 0-arg callable to invoke.
        description: human-readable label used in event payloads.
        on_event: optional callback receiving ``{"type": ..., ...}`` dicts.
        retry_on: exception types that should trigger a retry.  ``None`` means
            any ``Exception`` other than ``KeyboardInterrupt`` / ``SystemExit``.
        config: override for :data:`DEFAULT_RETRY` keys.
    """

    cfg = {**DEFAULT_RETRY, **(config or {})}
    retry_on_tuple: tuple[type[BaseException], ...]
    if retry_on is None:
        retry_on_tuple = (Exception,)
    elif isinstance(retry_on, type) and issubclass(retry_on, BaseException):
        retry_on_tuple = (retry_on,)
    else:
        retry_on_tuple = tuple(retry_on)  # type: ignore[arg-type]

    last_exc: BaseException | None = None
    for attempt in range(1, cfg["max_attempts"] + 1):
        try:
            return func()
        except retry_on_tuple as exc:  # type: ignore[misc]
            last_exc = exc
            if attempt >= cfg["max_attempts"]:
                if on_event is not None:
                    on_event(
                        {
                            "type": EVENT_LOCAL_RETRY,
                            "description": description,
                            "attempt": attempt,
                            "exhausted": True,
                            "error": str(exc),
                        }
                    )
                raise
            sleep_for = _compute_backoff(
                attempt=attempt,
                initial_interval=cfg["initial_interval"],
                max_interval=cfg["max_interval"],
                backoff_factor=cfg["backoff_factor"],
                jitter=cfg["jitter"],
            )
            if on_event is not None:
                on_event(
                    {
                        "type": EVENT_LOCAL_RETRY,
                        "description": description,
                        "attempt": attempt,
                        "next_interval_s": sleep_for,
                        "error": str(exc),
                    }
                )
            logger.warning(
                "MinerU 调用失败 (attempt=%s/%s) description=%s err=%s; sleep=%.2fs",
                attempt,
                cfg["max_attempts"],
                description,
                exc,
                sleep_for,
            )
            time.sleep(sleep_for)
    # 不可达；保留以满足类型检查
    if last_exc is not None:
        raise last_exc
    return None


def request_parse_file(
    file_path: Path,
    config: MinerUConfig,
    *,
    return_images: bool,
    on_event: ProgressCallback | None = None,
) -> bytes:
    """Call the configured MinerU backend for a supported document and return ZIP bytes."""

    on_event = on_event or _noop_event
    mode = (config.api_mode or "local").strip().lower()
    if mode in {"precise", "cloud", "api", "mineru"}:
        return _request_precise_parse_file(file_path, config, on_event=on_event)
    if mode not in {"local", "self-hosted", "self_hosted"}:
        raise MinerUConfigError(
            f"不支持的 MinerU 调用模式：{config.api_mode}",
            api_mode=config.api_mode,
        )
    return _request_local_parse_file(
        file_path, config, return_images=return_images, on_event=on_event
    )


def _request_local_parse_file(
    file_path: Path,
    config: MinerUConfig,
    *,
    return_images: bool,
    on_event: ProgressCallback | None = None,
) -> bytes:
    """Call a self-hosted MinerU /file_parse service and return ZIP bytes.

    Wrapped in :func:`_retry_with_policy` with
    ``max_attempts=3, initial_interval=2.0, max_interval=10.0, jitter=True`` —
    与 langgraph ``RetryPolicy`` 语义一致，**只对**网络 / 协议错误
    （``MinerURequestError`` / ``requests.exceptions.*``）重试。
    """

    on_event = on_event or _noop_event
    if not config.api_base_url:
        raise MinerUConfigError("缺少 MINERU_API_BASE_URL，无法调用 MinerU 服务。")
    try:
        import requests
    except ImportError as exc:
        raise MinerUConfigError("调用 MinerU 需要安装 requests。") from exc

    data = build_request_data(config, return_images=return_images)
    url = config.api_base_url.rstrip("/") + "/file_parse"

    def _do_request() -> bytes:
        on_event(
            {
                "type": EVENT_LOCAL_REQUEST,
                "url": url,
                "file": file_path.name,
                "size_bytes": file_path.stat().st_size,
            }
        )
        started = time.perf_counter()
        try:
            with file_path.open("rb") as file_obj:
                response = requests.post(
                    url,
                    files={"files": (file_path.name, file_obj, _content_type_for_file(file_path))},
                    data=data,
                    timeout=config.request_timeout,
                )
        except requests.exceptions.RequestException as exc:
            # 网络层：5xx / 连接重置 / 超时 —— 可重试
            raise MinerURequestError(
                f"MinerU 本地服务请求失败：{exc}", url=url
            ) from exc
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if response.status_code >= 500:
            # 5xx 视为瞬时错误，可重试
            raise MinerURequestError(
                f"MinerU 本地服务返回 {response.status_code}",
                url=url,
                status_code=response.status_code,
            )
        if response.status_code >= 400:
            # 4xx 是协议 / 输入错误，**不**重试
            preview = response.text[:300] if response.text else ""
            raise MinerUConfigError(
                f"MinerU 本地服务返回 4xx：HTTP {response.status_code}; body={preview}",
                url=url,
                status_code=response.status_code,
            )
        content_type = response.headers.get("content-type", "")
        if "zip" not in content_type.lower() and not response.content.startswith(b"PK"):
            preview = response.text[:300] if response.text else ""
            raise MinerURequestError(
                f"MinerU 未返回 ZIP：content-type={content_type}; body={preview}",
                url=url,
                content_type=content_type,
            )
        on_event(
            {
                "type": EVENT_LOCAL_RESPONSE,
                "url": url,
                "file": file_path.name,
                "status_code": response.status_code,
                "elapsed_ms": elapsed_ms,
                "size_bytes": len(response.content),
            }
        )
        return response.content

    # 只对 transient 网络 / 5xx 错误重试；4xx / 配置错误不重试
    return _retry_with_policy(
        _do_request,
        description=f"mineru.local:POST {url}",
        on_event=on_event,
        retry_on=(
            MinerURequestError,
            requests.exceptions.RequestException,
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
        ),
    )


def _request_precise_parse_file(
    file_path: Path,
    config: MinerUConfig,
    *,
    on_event: ProgressCallback | None = None,
) -> bytes:
    """Call MinerU precise parsing API via signed upload, polling, and ZIP download."""

    on_event = on_event or _noop_event
    if not config.api_token:
        raise MinerUConfigError("缺少 MINERU_API_TOKEN，无法调用 MinerU 精准解析 API。")
    if not config.precise_base_url:
        raise MinerUConfigError("缺少 MINERU_PRECISE_BASE_URL，无法调用 MinerU 精准解析 API。")
    try:
        import requests
    except ImportError as exc:
        raise MinerUConfigError("调用 MinerU 需要安装 requests。") from exc

    base_url = config.precise_base_url.rstrip("/")
    headers = {
        "Authorization": f"Bearer {config.api_token}",
        "Content-Type": "application/json",
        "Accept": "*/*",
    }
    data_id = _safe_data_id(file_path)
    payload = _build_precise_batch_payload(file_path, config, data_id=data_id)

    on_event({"type": EVENT_PRECISE_APPLY, "file": file_path.name, "data_id": data_id})
    try:
        create_response = requests.post(
            f"{base_url}/api/v4/file-urls/batch",
            headers=headers,
            json=payload,
            timeout=config.request_timeout,
        )
    except requests.exceptions.RequestException as exc:
        raise MinerURequestError(
            f"MinerU 精准解析申请上传 URL 失败：{exc}", url=f"{base_url}/api/v4/file-urls/batch"
        ) from exc
    if create_response.status_code >= 500:
        raise MinerURequestError(
            f"MinerU 精准解析申请 URL 5xx：HTTP {create_response.status_code}",
            status_code=create_response.status_code,
        )
    if create_response.status_code >= 400:
        preview = getattr(create_response, "text", "")[:300]
        raise MinerUConfigError(
            f"MinerU 精准解析申请 URL 4xx：HTTP {create_response.status_code}; body={preview}",
            status_code=create_response.status_code,
        )
    try:
        create_payload = _json_payload(create_response)
    except MinerURequestError:
        raise
    except Exception as exc:  # json.JSONDecodeError 等
        raise MinerURequestError(f"MinerU 精准解析响应解析失败：{exc}") from exc
    _ensure_mineru_ok(create_payload, "申请 MinerU 精准解析上传 URL")
    batch_id = str(create_payload.get("data", {}).get("batch_id") or "")
    file_urls = create_payload.get("data", {}).get("file_urls") or []
    if not batch_id or not file_urls:
        raise MinerURequestError(
            f"MinerU 精准解析未返回 batch_id/file_urls：{create_payload}"
        )

    on_event({"type": EVENT_PRECISE_UPLOAD, "file": file_path.name, "url": file_urls[0]})
    try:
        with file_path.open("rb") as file_obj:
            upload_response = requests.put(
                file_urls[0],
                data=file_obj,
                timeout=config.request_timeout,
            )
    except requests.exceptions.RequestException as exc:
        raise MinerURequestError(
            f"MinerU 精准解析文件上传失败：{exc}", url=file_urls[0]
        ) from exc
    if upload_response.status_code >= 500:
        raise MinerURequestError(
            f"MinerU 精准解析文件上传 5xx：HTTP {upload_response.status_code}",
            url=file_urls[0],
            status_code=upload_response.status_code,
        )
    if upload_response.status_code not in {200, 201, 204}:
        # 4xx 视为配置 / 协议错误，不重试
        preview = getattr(upload_response, "text", "")[:300]
        raise MinerUConfigError(
            f"MinerU 精准解析文件上传 4xx：HTTP {upload_response.status_code}; body={preview}",
            url=file_urls[0],
            status_code=upload_response.status_code,
        )

    zip_url = _poll_precise_zip_url(
        requests_module=requests,
        base_url=base_url,
        headers=headers,
        batch_id=batch_id,
        data_id=data_id,
        config=config,
        on_event=on_event,
    )
    on_event({"type": EVENT_PRECISE_DOWNLOAD, "file": file_path.name, "url": zip_url})
    try:
        zip_response = requests.get(zip_url, timeout=config.request_timeout)
    except requests.exceptions.RequestException as exc:
        raise MinerURequestError(
            f"MinerU 精准解析下载 ZIP 失败：{exc}", url=zip_url
        ) from exc
    if zip_response.status_code >= 500:
        raise MinerURequestError(
            f"MinerU 精准解析下载 ZIP 5xx：HTTP {zip_response.status_code}",
            url=zip_url,
            status_code=zip_response.status_code,
        )
    if zip_response.status_code >= 400:
        preview = zip_response.text[:300] if zip_response.text else ""
        raise MinerUConfigError(
            f"MinerU 精准解析下载 ZIP 4xx：HTTP {zip_response.status_code}; body={preview}",
            url=zip_url,
            status_code=zip_response.status_code,
        )
    content_type = zip_response.headers.get("content-type", "")
    if "zip" not in content_type.lower() and not zip_response.content.startswith(b"PK"):
        preview = zip_response.text[:300] if zip_response.text else ""
        raise MinerURequestError(
            f"MinerU 精准解析未返回 ZIP：content-type={content_type}; body={preview}",
            url=zip_url,
            content_type=content_type,
        )
    return zip_response.content


def _build_precise_batch_payload(
    file_path: Path, config: MinerUConfig, *, data_id: str
) -> dict[str, Any]:
    options = dict(config.request_options)
    file_item: dict[str, Any] = {"name": file_path.name, "data_id": data_id}
    if options.get("page_ranges"):
        file_item["page_ranges"] = options["page_ranges"]
    elif options.get("page_range"):
        file_item["page_ranges"] = options["page_range"]
    if "is_ocr" in options:
        file_item["is_ocr"] = _as_bool(options["is_ocr"])

    payload: dict[str, Any] = {
        "files": [file_item],
        "model_version": options.get("model_version") or config.precise_model_version,
    }
    language = options.get("language") or options.get("lang_list")
    if language:
        payload["language"] = language
    if "enable_formula" in options:
        payload["enable_formula"] = _as_bool(options["enable_formula"])
    elif "formula_enable" in options:
        payload["enable_formula"] = _as_bool(options["formula_enable"])
    if "enable_table" in options:
        payload["enable_table"] = _as_bool(options["enable_table"])
    elif "table_enable" in options:
        payload["enable_table"] = _as_bool(options["table_enable"])
    if config.precise_extra_formats:
        payload["extra_formats"] = list(config.precise_extra_formats)
    for key in ("callback", "seed", "no_cache", "cache_tolerance"):
        if key in options:
            payload[key] = options[key]
    return payload


def _poll_precise_zip_url(
    *,
    requests_module: Any,
    base_url: str,
    headers: dict[str, str],
    batch_id: str,
    data_id: str,
    config: MinerUConfig,
    on_event: ProgressCallback | None = None,
) -> str:
    on_event = on_event or _noop_event
    deadline = time.monotonic() + config.precise_poll_timeout
    last_state = ""
    last_payload: dict[str, Any] = {}
    started = time.monotonic()
    poll_count = 0
    while time.monotonic() < deadline:
        try:
            response = requests_module.get(
                f"{base_url}/api/v4/extract-results/batch/{batch_id}",
                headers=headers,
                timeout=config.request_timeout,
            )
        except requests_module.exceptions.RequestException as exc:  # type: ignore[attr-defined]
            raise MinerURequestError(
                f"MinerU 精准解析轮询失败：{exc}",
                batch_id=batch_id,
            ) from exc
        if response.status_code >= 500:
            raise MinerURequestError(
                f"MinerU 精准解析轮询 5xx：HTTP {response.status_code}",
                batch_id=batch_id,
                status_code=response.status_code,
            )
        if response.status_code >= 400:
            preview = getattr(response, "text", "")[:300]
            raise MinerUConfigError(
                f"MinerU 精准解析轮询 4xx：HTTP {response.status_code}; body={preview}",
                batch_id=batch_id,
                status_code=response.status_code,
            )
        try:
            payload = _json_payload(response)
        except MinerURequestError:
            raise
        except Exception as exc:
            raise MinerURequestError(f"MinerU 精准解析轮询响应解析失败：{exc}") from exc
        _ensure_mineru_ok(payload, "查询 MinerU 精准解析结果")
        last_payload = payload
        result = _select_extract_result(payload.get("data", {}).get("extract_result"), data_id)
        state = str(result.get("state", "")).lower()
        last_state = state
        poll_count += 1
        on_event(
            {
                "type": EVENT_PRECISE_POLL,
                "state": state or "unknown",
                "poll_count": poll_count,
                "elapsed_s": round(time.monotonic() - started, 3),
                "batch_id": batch_id,
                "data_id": data_id,
                "err_msg": result.get("err_msg") or "",
            }
        )
        if state == "done":
            zip_url = str(result.get("full_zip_url") or "")
            if not zip_url:
                raise MinerURequestError(
                    f"MinerU 精准解析完成但未返回 full_zip_url：{payload}"
                )
            return zip_url
        if state == "failed":
            raise MinerURequestError(
                f"MinerU 精准解析失败：{result.get('err_msg') or payload}"
            )
        time.sleep(max(config.precise_poll_interval or 1.0, 0.1))
    raise MinerURequestError(
        f"MinerU 精准解析轮询超时：batch_id={batch_id}, last_state={last_state}, last_payload={last_payload}",
        batch_id=batch_id,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 批量辅助（设计文档 §3.3 问题 1 的答复：先不暴露为 tool，按需在 driver 层调用）
# ─────────────────────────────────────────────────────────────────────────────


def request_parse_files_parallel(
    file_paths: Sequence[Path],
    config: MinerUConfig,
    *,
    return_images: bool,
    on_event: ProgressCallback | None = None,
    max_workers: int = 2,
) -> list[bytes]:
    """并发解析多个本地文件。

    precise 模式下 MinerU 服务端支持 ``/api/v4/file-urls/batch`` 一次上传多个；
    本地模式只能客户端并发。本函数统一用 ``ThreadPoolExecutor`` 并发，
    适合上层 driver / 批处理脚本调用，**当前不作为 Deep Agents 工具暴露**。
    """

    from concurrent.futures import ThreadPoolExecutor, as_completed

    on_event = on_event or _noop_event
    results: list[bytes | None] = [None] * len(file_paths)
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        future_to_index = {
            pool.submit(
                request_parse_file,
                path,
                config,
                return_images=return_images,
                on_event=on_event,
            ): idx
            for idx, path in enumerate(file_paths)
        }
        for future in as_completed(future_to_index):
            idx = future_to_index[future]
            results[idx] = future.result()
    return [item if item is not None else b"" for item in results]


def request_parse_pdf(pdf_path: Path, config: MinerUConfig, *, return_images: bool) -> bytes:
    """Backward-compatible wrapper for PDF callers."""

    return request_parse_file(pdf_path, config, return_images=return_images)


# ─────────────────────────────────────────────────────────────────────────────
# 私有辅助
# ─────────────────────────────────────────────────────────────────────────────


def _select_extract_result(value: Any, data_id: str) -> dict[str, Any]:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and item.get("data_id") == data_id:
                return item
        for item in value:
            if isinstance(item, dict):
                return item
    if isinstance(value, dict):
        return value
    return {}


def _json_payload(response: Any) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        preview = getattr(response, "text", "")[:300]
        raise MinerURequestError(f"MinerU 返回非 JSON 响应：{preview}") from exc
    if not isinstance(payload, dict):
        raise MinerURequestError(f"MinerU 返回 JSON 结构异常：{payload}")
    return payload


def _ensure_mineru_ok(payload: dict[str, Any], action: str) -> None:
    if payload.get("code") not in (0, "0", None):
        raise MinerURequestError(f"{action}失败：{payload.get('msg') or payload}")


def _safe_data_id(file_path: Path) -> str:
    allowed = []
    for char in file_path.stem[:120]:
        allowed.append(char if char.isalnum() or char in {"_", "-", "."} else "_")
    return "".join(allowed).strip("._") or "document"


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


# 可被测试/外部直接验证的事件类型列表
__all__ = [
    "EVENT_LOCAL_REQUEST",
    "EVENT_LOCAL_RESPONSE",
    "EVENT_LOCAL_RETRY",
    "EVENT_PRECISE_APPLY",
    "EVENT_PRECISE_UPLOAD",
    "EVENT_PRECISE_POLL",
    "EVENT_PRECISE_DOWNLOAD",
    "DEFAULT_RETRY",
    "MinerUConfigError",
    "MinerUError",
    "MinerURequestError",
    "request_parse_file",
    "request_parse_files_parallel",
    "request_parse_pdf",
]
