"""LangSmith / LangGraph tracing helpers for nested subgraph invocations."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

METADATA_EXTRACTION_GRAPH_NAME = "metadata_extraction"
METADATA_EXTRACTION_TOOL_NAME = "extract_standard_metadata"


def _parent_agent_name(parent_config: RunnableConfig | None) -> str | None:
    if not parent_config:
        return None
    metadata = parent_config.get("metadata") or {}
    if isinstance(metadata, dict):
        return metadata.get("lc_agent_name") or metadata.get("langgraph_node")
    return None


def build_subgraph_runnable_config(
    parent_config: RunnableConfig | None,
    *,
    graph_name: str,
    tool_name: str,
    tool_call_id: str | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> RunnableConfig:
    """Merge parent callbacks/tags so a nested graph appears under the active run tree."""

    base: RunnableConfig = dict(parent_config or {})
    tags = list(base.get("tags") or [])
    for tag in (graph_name, "standard_document_assistant", "subgraph"):
        if tag not in tags:
            tags.append(tag)

    metadata: dict[str, Any] = dict(base.get("metadata") or {})
    metadata.update(
        {
            "graph_id": graph_name,
            "orchestration_tool": tool_name,
            "workflow": "standard_document_assistant",
        }
    )
    if tool_call_id:
        metadata["tool_call_id"] = tool_call_id
    parent_agent = _parent_agent_name(parent_config)
    if parent_agent:
        metadata["parent_agent"] = parent_agent
    if extra_metadata:
        metadata.update(extra_metadata)

    return {
        **base,
        "run_name": graph_name,
        "tags": tags,
        "metadata": metadata,
    }


def invoke_traced_graph(
    graph: Any,
    state: dict[str, Any],
    *,
    parent_config: RunnableConfig | None,
    graph_name: str = METADATA_EXTRACTION_GRAPH_NAME,
    tool_name: str = METADATA_EXTRACTION_TOOL_NAME,
    tool_call_id: str | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Invoke a compiled LangGraph subgraph with parent tracing context."""

    config = build_subgraph_runnable_config(
        parent_config,
        graph_name=graph_name,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        extra_metadata=extra_metadata,
    )
    return graph.invoke(state, config=config)


async def ainvoke_traced_graph(
    graph: Any,
    state: dict[str, Any],
    *,
    parent_config: RunnableConfig | None,
    graph_name: str = METADATA_EXTRACTION_GRAPH_NAME,
    tool_name: str = METADATA_EXTRACTION_TOOL_NAME,
    tool_call_id: str | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Async variant of :func:`invoke_traced_graph`."""

    config = build_subgraph_runnable_config(
        parent_config,
        graph_name=graph_name,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        extra_metadata=extra_metadata,
    )
    return await graph.ainvoke(state, config=config)
