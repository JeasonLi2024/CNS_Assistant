from fastapi.testclient import TestClient

from standard_document_assistant.api.app import app
from standard_document_assistant.api.sse_adapter import map_langgraph_part


def test_health_endpoint() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["assistant_id"] == "agent"


def test_upload_endpoint_saves_file(tmp_path, monkeypatch) -> None:
    import standard_document_assistant.pathing as pathing_mod
    import standard_document_assistant.uploads as uploads_mod

    workspace_root = tmp_path / "workspace"
    uploads_dir = workspace_root / "input" / "uploads"
    monkeypatch.setattr(pathing_mod, "WORKSPACE_ROOT", workspace_root)
    monkeypatch.setattr(uploads_mod, "UPLOADS_DIR", uploads_dir)

    client = TestClient(app)
    response = client.post(
        "/api/threads/api-phase1/uploads",
        files={"file": ("standard.md", b"# standard", "text/markdown")},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["virtual_path"].startswith("/workspace/input/uploads/api-phase1/")
    assert payload["stored_filename"].endswith(".md")


def test_sse_adapter_maps_custom_progress() -> None:
    mapped = map_langgraph_part(
        {"event": "custom", "data": {"type": "mineru.parse.completed"}},
        run_id="run_test",
        thread_id="thread_test",
        seen_message_ids=set(),
    )
    assert mapped == [
        {
            "event": "agent.progress",
            "data": {
                "type": "mineru.parse.completed",
                "run_id": "run_test",
                "thread_id": "thread_test",
            },
        }
    ]


def test_sse_adapter_maps_interrupt() -> None:
    mapped = map_langgraph_part(
        {"event": "updates", "data": {"agent": {"__interrupt__": [{"value": "approve"}]}}},
        run_id="run_test",
        thread_id="thread_test",
        seen_message_ids=set(),
    )
    assert mapped[0]["event"] == "approval.required"
    assert mapped[0]["data"]["interrupt"] == [{"value": "approve"}]
