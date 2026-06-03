"""State schema for the metadata extraction graph."""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict


class MetadataExtractionState(TypedDict, total=False):
    source_path: str
    source_virtual_path: str
    markdown: str
    scope_mode: Literal["metadata", "full"]
    output_filename: str
    write_artifacts: bool
    output_path: str
    output_virtual_path: str
    annotated_path: str
    annotated_virtual_path: str
    normalized_path: str
    normalized_virtual_path: str
    manifest_path: str
    manifest_virtual_path: str
    scoped_text: str
    scoped_text_chars: int
    langextract_result: Any
    aggregated: dict[str, Any]
    cover_metadata_hint: dict[str, Any]
    validation: dict[str, Any]
    quality_warnings: Annotated[list[str], operator.add]
    errors: Annotated[list[str], operator.add]
    warnings: Annotated[list[str], operator.add]
    status: Literal["ok", "failed"]
    extracted_items: int


class MetadataExtractionContext(TypedDict, total=False):
    """Runtime context for the metadata extraction subgraph (Deep Agents best practice).

    依据 ``deep-agents-core`` skill 与 [LangGraph Runtime context](https://docs.langchain.com/oss/python/langgraph/graph-api#runtime-context)：
    - 在 ``builder.compile(context_schema=...)`` 时声明，由 ``graph.invoke(..., context=...)`` 注入；
    - 节点通过 ``Runtime[MetadataExtractionContext]`` 读取，避免污染 graph state；
    - 命名空间由 LangGraph 自动按 thread_id 隔离（参考 langgraph-persistence skill）；
    - Tool 层从 ``ToolRuntime.context`` 透传到 ``graph.invoke(..., context=...)``。
    """

    tenant_id: str
    user_id: str
    trace_id: str
    """透传主图 trace_id，便于子图节点在 LangSmith / 日志中关联父 run。"""
    quality_strict: bool
    """是否启用 strict_validation；不传则按 config.metadata_extraction.strict_validation 兜底。"""
    parent_tool_call_id: str | None
    """extractor subagent 调用本工具的 tool_call_id，仅用于 trace 关联。"""
    cover_metadata_hint: dict[str, Any]
    """由 parser 透传的封面元数据 hint；可从 context 注入，避免污染 state。"""
