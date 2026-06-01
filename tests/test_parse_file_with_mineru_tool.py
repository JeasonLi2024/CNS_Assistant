import io
import sys
import zipfile
from types import SimpleNamespace

from standard_document_assistant.config import MinerUConfig
from standard_document_assistant.integrations.mineru.client import request_parse_file
from standard_document_assistant.pathing import host_to_virtual_path
from standard_document_assistant.tools import parse_file_with_mineru
from standard_document_assistant.uploads import save_uploaded_file


def test_parse_file_with_mineru_accepts_docx(monkeypatch) -> None:
    from standard_document_assistant.tools import parser as parser_module

    record = save_uploaded_file(
        original_filename="parse-source.docx",
        content=b"docx bytes",
        thread_id="test-parse-docx",
    )

    def fake_request_parse_file(file_path, config, *, return_images):
        assert file_path.suffix == ".docx"
        return b"PK fake zip"

    def fake_parse_result_zip(
        *,
        zip_bytes,
        source_stem,
        output_root,
        return_images,
        save_middle_json,
        save_content_list,
    ):
        md_path = output_root / "md" / f"{source_stem}.md"
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text("# parsed", encoding="utf-8")
        return {
            "md_path": md_path,
            "image_root": None,
            "cover_metadata": {"source": "docx"},
            "artifacts": [
                {
                    "type": "markdown",
                    "virtual_path": host_to_virtual_path(md_path),
                    "description": "MinerU Markdown",
                }
            ],
        }

    monkeypatch.setattr(parser_module, "request_parse_file", fake_request_parse_file)
    monkeypatch.setattr(parser_module, "parse_result_zip", fake_parse_result_zip)

    result = parse_file_with_mineru.invoke({"file_path": record.virtual_path})

    assert result["status"] == "ok"
    assert result["source_virtual_path"].endswith(".docx")
    assert result["virtual_md_path"].endswith(".md")
    assert result["virtual_manifest_path"].startswith("/workspace/output/mineru/manifests/")


def test_precise_mineru_client_uses_signed_upload_and_downloads_zip(
    monkeypatch, tmp_path
) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("full.md", "# parsed")

    calls = []

    class FakeResponse:
        def __init__(
            self,
            payload=None,
            *,
            content=b"",
            status_code=200,
            headers=None,
            text="",
        ):
            self._payload = payload
            self.content = content
            self.status_code = status_code
            self.headers = headers or {}
            self.text = text

        def json(self):
            return self._payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"http {self.status_code}")

    def fake_post(url, *, headers, json, timeout):
        calls.append(("post", url, headers, json, timeout))
        assert url == "https://mineru.net/api/v4/file-urls/batch"
        assert headers["Authorization"] == "Bearer token"
        assert json["model_version"] == "vlm"
        assert json["language"] == "ch"
        assert json["enable_formula"] is True
        assert json["enable_table"] is True
        assert json["files"][0]["name"] == "source.pdf"
        return FakeResponse(
            {"code": 0, "data": {"batch_id": "batch-1", "file_urls": ["https://upload"]}}
        )

    def fake_put(url, *, data, timeout):
        calls.append(("put", url, data.read(), timeout))
        assert url == "https://upload"
        return FakeResponse(status_code=200)

    def fake_get(url, *, headers=None, timeout):
        calls.append(("get", url, timeout))
        if url.endswith("/api/v4/extract-results/batch/batch-1"):
            return FakeResponse(
                {
                    "code": 0,
                    "data": {
                        "batch_id": "batch-1",
                        "extract_result": [
                            {
                                "data_id": "source",
                                "file_name": "source.pdf",
                                "state": "done",
                                "full_zip_url": "https://cdn/result.zip",
                            }
                        ],
                    },
                }
            )
        return FakeResponse(content=buffer.getvalue(), headers={"content-type": "application/zip"})

    monkeypatch.setitem(
        sys.modules,
        "requests",
        SimpleNamespace(post=fake_post, put=fake_put, get=fake_get),
    )

    config = MinerUConfig(
        api_mode="precise",
        api_token="token",
        precise_poll_interval=0.1,
        request_options={
            "lang_list": "ch",
            "formula_enable": "true",
            "table_enable": "true",
        },
    )

    result = request_parse_file(source, config, return_images=True)

    assert result == buffer.getvalue()
    assert [item[0] for item in calls] == ["post", "put", "get", "get"]
