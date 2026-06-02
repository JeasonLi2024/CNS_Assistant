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
"""

from __future__ import annotations

from functools import lru_cache

from langgraph.graph import END, START, StateGraph

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
from standard_document_assistant.graphs.standard_review.state import StandardReviewState
from standard_document_assistant.tracing import STANDARD_REVIEW_GRAPH_NAME


@lru_cache(maxsize=1)
def get_standard_review_graph():
    builder = StateGraph(StandardReviewState)
    builder.add_node("ingest", ingest)
    builder.add_node("retrieve_rules", retrieve_rules)
    builder.add_node("judge_rules", judge_rules)
    builder.add_node("widen_review_scope", widen_review_scope)
    builder.add_node("reload_review_rules", reload_review_rules)
    builder.add_node("format_review", format_review)
    builder.add_node("aggregate", aggregate)
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
    return builder.compile(name=STANDARD_REVIEW_GRAPH_NAME)
