"""HTTP client for MinerU."""

from __future__ import annotations

import mimetypes
import time
from pathlib import Path
from typing import Any

from standard_document_assistant.config import MinerUConfig
from standard_document_assistant.integrations.mineru.config import build_request_data


def _content_type_for_file(file_path: Path) -> str:
    if file_path.suffix.lower() == ".docx":
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if file_path.suffix.lower() == ".pdf":
        return "application/pdf"
    return mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"


def request_parse_file(file_path: Path, config: MinerUConfig, *, return_images: bool) -> bytes:
    """Call the configured MinerU backend for a supported document and return ZIP bytes."""

    mode = (config.api_mode or "local").strip().lower()
    if mode in {"precise", "cloud", "api", "mineru"}:
        return _request_precise_parse_file(file_path, config)
    if mode not in {"local", "self-hosted", "self_hosted"}:
        raise ValueError(f"不支持的 MinerU 调用模式：{config.api_mode}")
    return _request_local_parse_file(file_path, config, return_images=return_images)


def _request_local_parse_file(file_path: Path, config: MinerUConfig, *, return_images: bool) -> bytes:
    """Call a self-hosted MinerU /file_parse service and return ZIP bytes."""

    if not config.api_base_url:
        raise RuntimeError("缺少 MINERU_API_BASE_URL，无法调用 MinerU 服务。")
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("调用 MinerU 需要安装 requests。") from exc

    data = build_request_data(config, return_images=return_images)
    url = config.api_base_url.rstrip("/") + "/file_parse"
    with file_path.open("rb") as file_obj:
        response = requests.post(
            url,
            files={"files": (file_path.name, file_obj, _content_type_for_file(file_path))},
            data=data,
            timeout=config.request_timeout,
        )
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    if "zip" not in content_type.lower() and not response.content.startswith(b"PK"):
        preview = response.text[:300] if response.text else ""
        raise RuntimeError(f"MinerU 未返回 ZIP：content-type={content_type}; body={preview}")
    return response.content


def _request_precise_parse_file(file_path: Path, config: MinerUConfig) -> bytes:
    """Call MinerU precise parsing API via signed upload, polling, and ZIP download."""

    if not config.api_token:
        raise RuntimeError("缺少 MINERU_API_TOKEN，无法调用 MinerU 精准解析 API。")
    if not config.precise_base_url:
        raise RuntimeError("缺少 MINERU_PRECISE_BASE_URL，无法调用 MinerU 精准解析 API。")
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("调用 MinerU 需要安装 requests。") from exc

    base_url = config.precise_base_url.rstrip("/")
    headers = {
        "Authorization": f"Bearer {config.api_token}",
        "Content-Type": "application/json",
        "Accept": "*/*",
    }
    data_id = _safe_data_id(file_path)
    payload = _build_precise_batch_payload(file_path, config, data_id=data_id)
    create_response = requests.post(
        f"{base_url}/api/v4/file-urls/batch",
        headers=headers,
        json=payload,
        timeout=config.request_timeout,
    )
    create_response.raise_for_status()
    create_payload = _json_payload(create_response)
    _ensure_mineru_ok(create_payload, "申请 MinerU 精准解析上传 URL")
    batch_id = str(create_payload.get("data", {}).get("batch_id") or "")
    file_urls = create_payload.get("data", {}).get("file_urls") or []
    if not batch_id or not file_urls:
        raise RuntimeError(f"MinerU 精准解析未返回 batch_id/file_urls：{create_payload}")

    with file_path.open("rb") as file_obj:
        upload_response = requests.put(
            file_urls[0],
            data=file_obj,
            timeout=config.request_timeout,
        )
    if upload_response.status_code not in {200, 201, 204}:
        preview = getattr(upload_response, "text", "")[:300]
        raise RuntimeError(
            f"MinerU 精准解析文件上传失败：HTTP {upload_response.status_code}; body={preview}"
        )

    zip_url = _poll_precise_zip_url(
        requests_module=requests,
        base_url=base_url,
        headers=headers,
        batch_id=batch_id,
        data_id=data_id,
        config=config,
    )
    zip_response = requests.get(zip_url, timeout=config.request_timeout)
    zip_response.raise_for_status()
    content_type = zip_response.headers.get("content-type", "")
    if "zip" not in content_type.lower() and not zip_response.content.startswith(b"PK"):
        preview = zip_response.text[:300] if zip_response.text else ""
        raise RuntimeError(f"MinerU 精准解析未返回 ZIP：content-type={content_type}; body={preview}")
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
) -> str:
    deadline = time.monotonic() + config.precise_poll_timeout
    last_state = ""
    last_payload: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = requests_module.get(
            f"{base_url}/api/v4/extract-results/batch/{batch_id}",
            headers=headers,
            timeout=config.request_timeout,
        )
        response.raise_for_status()
        payload = _json_payload(response)
        _ensure_mineru_ok(payload, "查询 MinerU 精准解析结果")
        last_payload = payload
        result = _select_extract_result(payload.get("data", {}).get("extract_result"), data_id)
        state = str(result.get("state", "")).lower()
        last_state = state
        if state == "done":
            zip_url = str(result.get("full_zip_url") or "")
            if not zip_url:
                raise RuntimeError(f"MinerU 精准解析完成但未返回 full_zip_url：{payload}")
            return zip_url
        if state == "failed":
            raise RuntimeError(f"MinerU 精准解析失败：{result.get('err_msg') or payload}")
        time.sleep(max(config.precise_poll_interval or 1.0, 0.1))
    raise TimeoutError(
        f"MinerU 精准解析轮询超时：batch_id={batch_id}, last_state={last_state}, last_payload={last_payload}"
    )


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
        raise RuntimeError(f"MinerU 返回非 JSON 响应：{preview}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"MinerU 返回 JSON 结构异常：{payload}")
    return payload


def _ensure_mineru_ok(payload: dict[str, Any], action: str) -> None:
    if payload.get("code") not in (0, "0", None):
        raise RuntimeError(f"{action}失败：{payload.get('msg') or payload}")


def _safe_data_id(file_path: Path) -> str:
    allowed = []
    for char in file_path.stem[:120]:
        allowed.append(char if char.isalnum() or char in {"_", "-", "."} else "_")
    return "".join(allowed).strip("._") or "document"


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def request_parse_pdf(pdf_path: Path, config: MinerUConfig, *, return_images: bool) -> bytes:
    """Backward-compatible wrapper for PDF callers."""

    return request_parse_file(pdf_path, config, return_images=return_images)
