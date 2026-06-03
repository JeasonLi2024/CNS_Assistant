"""LangGraph SDK client factory for the FastAPI BFF."""

from __future__ import annotations

from functools import lru_cache

from langgraph_sdk import get_client

from standard_document_assistant.api.settings import get_settings


@lru_cache(maxsize=1)
def get_langgraph_client():
    """Return a cached async LangGraph client."""

    settings = get_settings()
    return get_client(url=settings.langgraph_api_url)
