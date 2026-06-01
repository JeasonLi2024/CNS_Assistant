import io
import zipfile

from standard_document_assistant.integrations.mineru.zip_parser import parse_result_zip
from standard_document_assistant.pathing import mineru_output_root


def test_parse_result_zip_persists_markdown_and_manifest_inputs() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("doc/result.md", "标准正式编号：GB/T 1234-2026\n\n# 测试标准")
        archive.writestr("doc/doc_middle.json", "{}")

    result = parse_result_zip(
        zip_bytes=buffer.getvalue(),
        source_stem="sample",
        output_root=mineru_output_root("pytest"),
        return_images=False,
        save_middle_json=False,
        save_content_list=False,
    )
    assert result["md_path"].exists()
    assert result["artifacts"][0]["virtual_path"].startswith("/workspace/output/mineru/pytest/md/")

