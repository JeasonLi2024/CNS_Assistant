"""Graph builder for the standard review pipeline.

Node order::

    ingest
       └── retrieve_rules
              └── judge_rules
                     ├── quality_gate
                     │     ├── (widen) widen_review_scope → reload_review_rules → judge_rules (loop)
                     │     └── (ok)   format_review
                     │                     └── aggregate
                     │                             ├── write_outputs
                     │                             └── write_manifest → END
```

`quality_gate` is a `Command[Literal[...]]` node: it both updates state and
chooses the next node in a single return. ``widen_review_scope`` and
``reload_review_rules`` are only entered when the gate has detected an
``insufficient_context`` issue and ``review_round < max_review_rounds``.

Deep Agents integration (2026-06-03 rev. 1)
------------------------------------------
- **context_schema=StandardReviewContext**：横切关注点（trace_id /
  tenant_id / job_id 等）从主 agent 透传到子图节点，避免污染 graph state。
  依据 LangGraph Runtime context：
  https://docs.langchain.com/oss/python/langgraph/graph-api#runtime-context
- **节点级 RetryPolicy**：``judge_rules`` / ``aggregate`` 是 LLM 调用的主要
  节点，对网络 / 协议类 transient 错误重试，避免对 LLM 输出解析错误无意义
  重试。依据：
  https://docs.langchain.com/oss/python/langgraph/use-graph-api#exception-handling
- **checkpointer 不显式挂**：依靠主图 ``compile(checkpointer=MemorySaver())``
  自动传播，命名空间由 thread_id 隔离。依据：
  https://docs.langchain.com/oss/python/langgraph/add-memory#use-in-subgraphs
"""

from __future__ import annotations

from functools import lru_cache

import requests
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from standard_document_assistant.graphs.standard_review.nodes.aggregate import aggregate
from standard_document_assistant.graphs.standard_review.nodes.format_review import format_review
from standard_document_assistant.graphs.standard_review.nodes.ingest import ingest
from standard_document_assistant.graphs.standard_review.nodes.report import write_manifest, write_outputs
from standard_document_assistant.graphs.standard_review.nodes.retrieve import retrieve_rules
from standard_document_assistant.graphs.standard_review.nodes.review import (
    judge_rules,
    quality_gate,
    reload_review_rules,
    widen_review_scope,
)
from standard_document_assistant.graphs.standard_review.state import (
    StandardReviewContext,
    StandardReviewState,
)
from standard_document_assistant.tracing import STANDARD_REVIEW_GRAPH_NAME


# 节点级 RetryPolicy：仅对网络 / 协议类 transient 错误重试，避免对 LLM 输出
# 解析错误无意义重试。依据 LangGraph 官方建议：
# https://docs.langchain.com/oss/python/langgraph/use-graph-api#exception-handling
# "Only failing branches are retried, so you needn't worry about performing
# redundant work."
_REVIEW_LLM_RETRY_POLICY = RetryPolicy(
    max_attempts=2,
    initial_interval=1.0,
    max_interval=10.0,
    jitter=True,
    retry_on=(requests.exceptions.RequestException, RuntimeError),
)


@lru_cache(maxsize=1)
def get_standard_review_graph():
    # context_schema 落地：节点通过 Runtime[StandardReviewContext] 读取 trace_id
    # / tenant_id / job_id 等横切关注点，避免污染 graph state。依据
    # [LangGraph Runtime context](https://docs.langchain.com/oss/python/langgraph/graph-api#runtime-context)
    # 与 [Deep Agents Runtime context](https://docs.langchain.com/oss/python/deepagents/context-engineering#runtime-context)。
    # langgraph 1.2+ API：context_schema 在 StateGraph() 构造时声明，compile() 不可再传。
    builder = StateGraph(
        StandardReviewState,
        context_schema=StandardReviewContext,
    )
    builder.add_node("ingest", ingest)
    builder.add_node("retrieve_rules", retrieve_rules)
    # judge_rules 是子图内主 LLM 审核节点（LLMSoftRuleJudge.run_dual_route），
    # 挂 RetryPolicy 应对上游模型限流 / 网络瞬断。
    builder.add_node(
        "judge_rules",
        judge_rules,
        retry_policy=_REVIEW_LLM_RETRY_POLICY,
    )
    builder.add_node("widen_review_scope", widen_review_scope)
    builder.add_node("reload_review_rules", reload_review_rules)
    builder.add_node("format_review", format_review)
    # aggregate 节点内可能调用 LLM 总结（如有），同样挂 RetryPolicy
    builder.add_node(
        "aggregate",
        aggregate,
        retry_policy=_REVIEW_LLM_RETRY_POLICY,
    )
    builder.add_node("write_outputs", write_outputs)
    builder.add_node("write_manifest", write_manifest)
    builder.add_node("quality_gate", quality_gate)

    builder.add_edge(START, "ingest")
    builder.add_edge("ingest", "retrieve_rules")
    builder.add_edge("retrieve_rules", "judge_rules")
    builder.add_edge("judge_rules", "quality_gate")
    builder.add_edge("widen_review_scope", "reload_review_rules")
    builder.add_edge("reload_review_rules", "judge_rules")
    builder.add_edge("format_review", "aggregate")
    builder.add_edge("aggregate", "write_outputs")
    builder.add_edge("write_outputs", "write_manifest")
    builder.add_edge("write_manifest", END)

    # checkpointer 不显式挂：依靠主图 compile(checkpointer=MemorySaver()) 自动传播，
    # 命名空间由 thread_id 隔离。依据
    # [Use in subgraphs](https://docs.langchain.com/oss/python/langgraph/add-memory#use-in-subgraphs)。
    return builder.compile(name=STANDARD_REVIEW_GRAPH_NAME)
