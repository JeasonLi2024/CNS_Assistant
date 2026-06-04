import importlib

from fastapi.testclient import TestClient

from standard_document_assistant.api.app import app
from standard_document_assistant.api.models import DirectStandardReviewRequest
from standard_document_assistant.api.sse_adapter import map_langgraph_part


app_mod = importlib.import_module("standard_document_assistant.api.app")


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


def test_direct_review_state_maps_scoped_options() -> None:
    payload = DirectStandardReviewRequest.model_validate(
        {
            "thread_id": "review-test",
            "file_path": "/workspace/input/uploads/review-test/draft.md",
            "review_options": {
                "mode": "scoped_content",
                "target_scopes": ["scope", "normative_references"],
                "disable_widen": True,
            },
        }
    )
    state = app_mod._build_direct_review_state(payload)
    assert state["content_path"] == "/workspace/input/uploads/review-test/draft.md"
    assert state["target_scopes"] == ["scope", "normative_references"]
    assert state["partial_mode"] == "sectional"
    assert state["max_review_rounds"] == 0
    assert state["format_only"] is False


def test_direct_review_state_maps_format_only() -> None:
    payload = DirectStandardReviewRequest.model_validate(
        {
            "thread_id": "review-test",
            "file_path": "/workspace/input/uploads/review-test/draft.docx",
            "review_options": {"mode": "format_only"},
        }
    )
    state = app_mod._build_direct_review_state(payload)
    assert state["content_path"] == ""
    assert state["source_path"] == "/workspace/input/uploads/review-test/draft.docx"
    assert state["format_only"] is True
    assert state["partial_mode"] == "format_only"


def test_direct_review_endpoint_calls_standard_review(monkeypatch) -> None:
    calls = {}

    class FakeRuns:
        async def wait(self, thread_id, assistant_id, *, input, raise_error):
            calls["thread_id"] = thread_id
            calls["assistant_id"] = assistant_id
            calls["input"] = input
            calls["raise_error"] = raise_error
            return {
                "job_id": "job_test",
                "trace_id": "trace_test",
                "status": "success",
                "aggregate_summary": {"total_issues": 0, "failed": 0},
                "output_paths": {},
            }

    class FakeClient:
        runs = FakeRuns()

    monkeypatch.setattr(app_mod, "get_langgraph_client", lambda: FakeClient())
    monkeypatch.setattr(
        app_mod,
        "_direct_review_response",
        lambda *, thread_id, payload, state_result: {
            "status": "completed",
            "thread_id": thread_id,
            "passed": True,
            "review_options": payload.review_options.model_dump(),
        },
    )

    client = TestClient(app)
    response = client.post(
        "/api/review-jobs/standard-review",
        json={
            "thread_id": "review-test",
            "file_path": "/workspace/input/uploads/review-test/draft.md",
            "review_options": {
                "mode": "scoped_content",
                "target_scopes": ["scope", "normative_references"],
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["passed"] is True
    assert calls["assistant_id"] == "standard_review"
    assert calls["raise_error"] is True
    assert calls["input"]["target_scopes"] == ["scope", "normative_references"]


def test_direct_review_endpoint_returns_502_on_upstream_error(monkeypatch) -> None:
    class FakeRuns:
        async def wait(self, thread_id, assistant_id, *, input, raise_error):
            raise PermissionError("workspace output denied")

    class FakeClient:
        runs = FakeRuns()

    monkeypatch.setattr(app_mod, "get_langgraph_client", lambda: FakeClient())

    client = TestClient(app)
    response = client.post(
        "/api/review-jobs/standard-review",
        json={
            "thread_id": "019e9086-917d-7050-be79-d18e651e33a4",
            "file_path": "/workspace/input/uploads/review-test/draft.md",
            "review_options": {
                "mode": "scoped_content",
                "target_scopes": ["foreword"],
                "disable_widen": True,
            },
        },
    )
    assert response.status_code == 502
    assert "标准审核执行失败" in response.json()["detail"]
