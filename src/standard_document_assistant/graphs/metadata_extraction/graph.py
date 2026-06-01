"""Graph builder for metadata extraction."""

from __future__ import annotations

from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from standard_document_assistant.graphs.metadata_extraction.nodes import (
    aggregate_fields,
    load_markdown,
    persist_output,
    run_langextract,
    slice_scope,
    validate_schema,
    write_manifest,
)
from standard_document_assistant.graphs.metadata_extraction.state import MetadataExtractionState
from standard_document_assistant.tracing import METADATA_EXTRACTION_GRAPH_NAME


@lru_cache(maxsize=1)
def get_metadata_extraction_graph():
    builder = StateGraph(MetadataExtractionState)
    builder.add_node("load_markdown", load_markdown)
    builder.add_node("slice_scope", slice_scope)
    builder.add_node("run_langextract", run_langextract)
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
    return builder.compile(name=METADATA_EXTRACTION_GRAPH_NAME)

