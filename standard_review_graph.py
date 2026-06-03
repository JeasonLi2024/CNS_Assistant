"""LangGraph Server entrypoint for the standard review subgraph (Studio / LangSmith).

注册到 ``langgraph.json`` 的 ``standard_review`` 槽位后,可在
``langgraph dev`` Studio 中看到完整子图拓扑:
``ingest -> retrieve_rules -> judge_rules -> quality_gate -> (widen|ok) -> ... -> aggregate -> write_outputs -> write_manifest -> END``.
"""

from standard_document_assistant.graphs.standard_review.graph import (
    get_standard_review_graph,
)

# Graph ID in langgraph.json: standard_review
standard_review = get_standard_review_graph()
