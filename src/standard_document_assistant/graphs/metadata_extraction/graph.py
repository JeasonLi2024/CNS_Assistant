"""Graph builder for metadata extraction."""

from __future__ import annotations

from functools import lru_cache

import requests
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from standard_document_assistant.graphs.metadata_extraction.nodes import (
    aggregate_fields,
    load_markdown,
    persist_output,
    run_langextract,
    slice_scope,
    validate_schema,
    write_manifest,
)
from standard_document_assistant.graphs.metadata_extraction.state import (
    MetadataExtractionContext,
    MetadataExtractionState,
)
from standard_document_assistant.tracing import METADATA_EXTRACTION_GRAPH_NAME


# 节点级 RetryPolicy：仅对网络 / 协议类 transient 错误重试，避免对 LLM 输出
# 解析错误无意义重试。依据 LangGraph 官方建议：
# https://docs.langchain.com/oss/python/langgraph/use-graph-api#exception-handling
# "Only failing branches are retried, so you needn't worry about performing
# redundant work."
_LANGEXTRACT_RETRY_POLICY = RetryPolicy(
    max_attempts=2,
    initial_interval=1.0,
    max_interval=10.0,
    jitter=True,
    retry_on=(requests.exceptions.RequestException, RuntimeError),
)


@lru_cache(maxsize=1)
def get_metadata_extraction_graph():
    # context_schema 落地：节点通过 Runtime[MetadataExtractionContext] 读取 trace_id
    # / cover_metadata_hint / quality_strict 等横切关注点，避免污染 graph state。
    # 依据 [LangGraph Runtime context](https://docs.langchain.com/oss/python/langgraph/graph-api#runtime-context)
    # 与 [Deep Agents Runtime context](https://docs.langchain.com/oss/python/deepagents/context-engineering#runtime-context)。
    # langgraph 1.2+ API：context_schema 在 StateGraph() 构造时声明，compile() 不可再传。
    builder = StateGraph(
        MetadataExtractionState,
        context_schema=MetadataExtractionContext,
    )
    builder.add_node("load_markdown", load_markdown)
    builder.add_node("slice_scope", slice_scope)
    # run_langextract 是子图内唯一一次外部 LLM 调用节点，挂 RetryPolicy
    builder.add_node(
        "run_langextract",
        run_langextract,
        retry_policy=_LANGEXTRACT_RETRY_POLICY,
    )
    builder.add_node("aggregate_fields", aggregate_fields)
    builder.add_node("validate_schema", validate_schema)
    builder.add_node("persist_output", persist_output)
    builder.add_node("write_manifest", write_manifest)
    builder.add_edge(START, "load_markdown")
    builder.add_edge("load_markdown", "slice_scope")
    builder.add_edge("slice_scope", "run_langextract")
    builder.add_edge("run_langextract", "aggregate_fields")
    builder.add_edge("aggregate_fields", "validate_schema")
    builder.add_edge("validate_schema", "persist_output")
    builder.add_edge("persist_output", "write_manifest")
    builder.add_edge("write_manifest", END)

    # checkpointer 不显式挂：依靠主图 compile(checkpointer=MemorySaver()) 自动传播，
    # 命名空间由 thread_id 隔离。依据
    # [Use in subgraphs](https://docs.langchain.com/oss/python/langgraph/add-memory#use-in-subgraphs)。
    return builder.compile(name=METADATA_EXTRACTION_GRAPH_NAME)

