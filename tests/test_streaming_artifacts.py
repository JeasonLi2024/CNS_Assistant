from standard_document_assistant.streaming import map_tool_completed
from standard_document_assistant.constants import METADATA_OUTPUT_DIR
from standard_document_assistant.pathing import host_to_virtual_path


def test_map_tool_completed_emits_artifact_created(monkeypatch) -> None:
    monkeypatch.setenv("STANDARD_DOC_ARTIFACT_API_BASE", "http://127.0.0.1:8080")
    METADATA_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = METADATA_OUTPUT_DIR / "json" / "streaming_artifact_test.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    virtual = host_to_virtual_path(path)

    events = map_tool_completed(
        run_id="run_test",
        thread_id="thread-stream",
        tool_name="extract_standard_metadata",
        tool_result={
            "status": "ok",
            "virtual_output_path": virtual,
        },
    )
    assert any(item["event"] == "artifact.created" for item in events)
    created = next(item for item in events if item["event"] == "artifact.created")
    assert created["data"]["thread_id"] == "thread-stream"
    assert "host_path" not in created["data"]
