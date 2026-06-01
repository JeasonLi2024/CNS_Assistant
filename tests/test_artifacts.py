import json

import pytest

from standard_document_assistant.artifacts import (
    build_download_url,
    describe_downloadable_artifact,
    get_thread_artifact,
    list_thread_artifacts,
    public_artifact_record,
    register_from_tool_result,
    register_thread_artifact,
    resolve_thread_artifact_path,
)
from standard_document_assistant.constants import ARTIFACTS_DIR, METADATA_OUTPUT_DIR
from standard_document_assistant.pathing import host_to_virtual_path


def _write_metadata_json(name: str = "artifact_registry_test.json") -> str:
    METADATA_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = METADATA_OUTPUT_DIR / "json" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"标准号":"GB/T 1-2026"}', encoding="utf-8")
    return host_to_virtual_path(path)


def test_register_thread_artifact_writes_manifest(monkeypatch) -> None:
    monkeypatch.setenv("STANDARD_DOC_ARTIFACT_API_BASE", "http://127.0.0.1:8080")
    virtual = _write_metadata_json()
    record = register_thread_artifact(
        thread_id="thread-a",
        virtual_path=virtual,
        tool="extract_standard_metadata",
        artifact_type="metadata_json",
        description="测试 JSON",
    )
    assert record.artifact_id
    assert record.download_url == build_download_url("thread-a", record.artifact_id)
    manifest_path = ARTIFACTS_DIR / "thread-a" / "artifact_manifest.json"
    assert manifest_path.exists()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(data["artifacts"]) == 1


def test_list_and_resolve_thread_artifact() -> None:
    virtual = _write_metadata_json("artifact_registry_test_2.json")
    record = register_thread_artifact(
        thread_id="thread-b",
        virtual_path=virtual,
        tool="extract_standard_metadata",
        artifact_type="metadata_json",
    )
    listed = list_thread_artifacts("thread-b")
    assert len(listed) == 1
    fetched = get_thread_artifact("thread-b", record.artifact_id)
    assert fetched is not None
    assert fetched.virtual_path == virtual
    host = resolve_thread_artifact_path("thread-b", record.artifact_id)
    assert host.exists()


def test_register_from_tool_result_skips_missing_files() -> None:
    virtual = _write_metadata_json("artifact_registry_test_3.json")
    records = register_from_tool_result(
        thread_id="thread-c",
        tool_name="extract_standard_metadata",
        tool_result={
            "status": "ok",
            "source_virtual_path": "/workspace/input/uploads/thread-c/sample.md",
            "virtual_output_path": virtual,
            "virtual_manifest_path": "/workspace/output/metadata/manifests/missing.json",
        },
    )
    assert len(records) == 1
    assert records[0].artifact_type == "metadata_json"


def test_public_artifact_record_hides_host_path_by_default(monkeypatch) -> None:
    virtual = _write_metadata_json("artifact_registry_test_4.json")
    record = register_thread_artifact(
        thread_id="thread-d",
        virtual_path=virtual,
        tool="extract_standard_metadata",
        artifact_type="metadata_json",
    )
    payload = public_artifact_record(record)
    assert "host_path" not in payload
    monkeypatch.setenv("STANDARD_DOC_EXPOSE_HOST_PATH", "1")
    payload = public_artifact_record(record)
    assert payload["host_path"]


def test_describe_downloadable_artifact_with_registered_ids(monkeypatch) -> None:
    virtual = _write_metadata_json("artifact_registry_test_5.json")
    monkeypatch.setenv("STANDARD_DOC_ARTIFACT_API_BASE", "http://127.0.0.1:8080")
    info = describe_downloadable_artifact(
        virtual,
        thread_id="thread-e",
        artifact_id="abc123",
    )
    assert info["download_url"] == "http://127.0.0.1:8080/api/threads/thread-e/artifacts/abc123/download"


def test_register_thread_artifact_rejects_non_output_path() -> None:
    with pytest.raises(ValueError):
        register_thread_artifact(
            thread_id="thread-f",
            virtual_path="/workspace/input/uploads/thread-f/sample.md",
            tool="extract_standard_metadata",
            artifact_type="metadata_json",
        )
