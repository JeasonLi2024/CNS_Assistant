from standard_document_assistant.tracing import (
    METADATA_EXTRACTION_GRAPH_NAME,
    METADATA_EXTRACTION_TOOL_NAME,
    build_subgraph_runnable_config,
)


def test_build_subgraph_runnable_config_merges_parent() -> None:
    parent = {
        "callbacks": ["parent-callback"],
        "tags": ["agent"],
        "metadata": {"lc_agent_name": "extractor", "thread_id": "t-1"},
    }
    child = build_subgraph_runnable_config(
        parent,
        graph_name=METADATA_EXTRACTION_GRAPH_NAME,
        tool_name=METADATA_EXTRACTION_TOOL_NAME,
        tool_call_id="call-123",
        extra_metadata={"source_virtual_path": "/workspace/input/x.md"},
    )
    assert child["run_name"] == METADATA_EXTRACTION_GRAPH_NAME
    assert child["callbacks"] == ["parent-callback"]
    assert "metadata_extraction" in child["tags"]
    assert child["metadata"]["parent_agent"] == "extractor"
    assert child["metadata"]["tool_call_id"] == "call-123"
    assert child["metadata"]["orchestration_tool"] == METADATA_EXTRACTION_TOOL_NAME
    assert child["metadata"]["source_virtual_path"] == "/workspace/input/x.md"
