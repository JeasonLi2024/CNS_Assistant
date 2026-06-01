"""HTTP client for MinerU."""

from __future__ import annotations

from pathlib import Path

from standard_document_assistant.config import MinerUConfig
from standard_document_assistant.integrations.mineru.config import build_request_data


def request_parse_pdf(pdf_path: Path, config: MinerUConfig, *, return_images: bool) -> bytes:
    """Call MinerU /file_parse and return ZIP bytes."""

    if not config.api_base_url:
        raise RuntimeError("缺少 MINERU_API_BASE_URL，无法调用 MinerU 服务。")
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("调用 MinerU 需要安装 requests。") from exc

    data = build_request_data(config, return_images=return_images)
    url = config.api_base_url.rstrip("/") + "/file_parse"
    with pdf_path.open("rb") as file_obj:
        response = requests.post(
            url,
            files={"files": (pdf_path.name, file_obj, "application/pdf")},
            data=data,
            timeout=config.request_timeout,
        )
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    if "zip" not in content_type.lower() and not response.content.startswith(b"PK"):
        preview = response.text[:300] if response.text else ""
        raise RuntimeError(f"MinerU 未返回 ZIP：content-type={content_type}; body={preview}")
    return response.content

