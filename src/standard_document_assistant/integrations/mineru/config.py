"""MinerU request configuration helpers."""

from __future__ import annotations

from typing import Any

from standard_document_assistant.config import MinerUConfig


def _bool_string(value: bool) -> str:
    return "true" if value else "false"


def build_request_data(config: MinerUConfig, *, return_images: bool) -> dict[str, Any]:
    data = dict(config.request_options)
    data["response_format_zip"] = "true"
    data["return_middle_json"] = "true"
    if return_images:
        data["return_images"] = "true"
        data["return_content_list"] = "true"
    else:
        data["return_images"] = _bool_string(False)
        data.setdefault("return_content_list", "true")
    return data

