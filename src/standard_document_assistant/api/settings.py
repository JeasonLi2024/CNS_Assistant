"""Settings for the local FastAPI BFF."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ApiSettings:
    """Runtime settings read from environment variables."""

    langgraph_api_url: str = "http://127.0.0.1:2024"
    assistant_id: str = "agent"
    artifact_api_base: str = "http://127.0.0.1:8080"
    default_stream_modes: tuple[str, ...] = ("custom", "updates")
    stream_subgraphs: bool = True


def _truthy(value: str | None, *, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_settings() -> ApiSettings:
    """Return API settings for the current process."""

    modes = os.getenv("STANDARD_DOC_STREAM_MODES", "custom,updates")
    return ApiSettings(
        langgraph_api_url=os.getenv("LANGGRAPH_API_URL", "http://127.0.0.1:2024").rstrip("/"),
        assistant_id=os.getenv("STANDARD_DOC_ASSISTANT_ID", "agent"),
        artifact_api_base=os.getenv(
            "STANDARD_DOC_ARTIFACT_API_BASE",
            "http://127.0.0.1:8080",
        ).rstrip("/"),
        default_stream_modes=tuple(item.strip() for item in modes.split(",") if item.strip()),
        stream_subgraphs=_truthy(os.getenv("STANDARD_DOC_STREAM_SUBGRAPHS"), default=True),
    )
