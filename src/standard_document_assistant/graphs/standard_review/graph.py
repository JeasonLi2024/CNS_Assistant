"""Graph builder for standard review."""

from __future__ import annotations

from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from standard_document_assistant.graphs.standard_review.nodes import (
    aggregate,
    content_review,
    format_review,
    ingest,
    retrieve_rules,
    write_manifest,
    write_report,
)
from standard_document_assistant.graphs.standard_review.state import StandardReviewState
from standard_document_assistant.tracing import STANDARD_REVIEW_GRAPH_NAME


@lru_cache(maxsize=1)
def get_standard_review_graph():
    builder = StateGraph(StandardReviewState)
    builder.add_node("ingest", ingest)
    builder.add_node("retrieve_rules", retrieve_rules)
    builder.add_node("content_review", content_review)
    builder.add_node("format_review", format_review)
    builder.add_node("aggregate", aggregate)
    builder.add_node("write_report", write_report)
    builder.add_node("write_manifest", write_manifest)
    builder.add_edge(START, "ingest")
    builder.add_edge("ingest", "retrieve_rules")
    builder.add_edge("retrieve_rules", "content_review")
    builder.add_edge("content_review", "format_review")
    builder.add_edge("format_review", "aggregate")
    builder.add_edge("aggregate", "write_report")
    builder.add_edge("write_report", "write_manifest")
    builder.add_edge("write_manifest", END)
    return builder.compile(name=STANDARD_REVIEW_GRAPH_NAME)

