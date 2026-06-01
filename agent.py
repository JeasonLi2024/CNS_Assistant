"""LangGraph Server entrypoint for ``langgraph dev`` and LangSmith Deployment."""

from standard_document_assistant.agent import build_standard_document_agent

# LangGraph loads this compiled graph by ID from langgraph.json.
agent = build_standard_document_agent(strict_model=True, langgraph_server=True)
