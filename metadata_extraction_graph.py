"""LangGraph Server entrypoint for the metadata extraction subgraph (Studio / LangSmith)."""

from standard_document_assistant.graphs.metadata_extraction.graph import (
    get_metadata_extraction_graph,
)

# Graph ID in langgraph.json: metadata_extraction
metadata_extraction = get_metadata_extraction_graph()
