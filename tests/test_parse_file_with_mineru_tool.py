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
