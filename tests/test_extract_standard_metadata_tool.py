from pathlib import Path

from standard_document_assistant.tools import extract_standard_metadata
from standard_document_assistant.uploads import save_uploaded_file


def test_extract_standard_metadata_from_uploaded_markdown() -> None:
    record = save_uploaded_file(
        original_filename="metadata.md",
        thread_id="test-extract",
        content=(
            "标准正式编号：GB/T 5678-2026\n"
            "ICS 03.120.99\n"
            "CCS A00\n\n"
            "# 元数据测试标准\n\n"
            "## 2 规范性引用文件\n\n"
            "- GB/T 1.1\n"
        ).encode("utf-8"),
    )
    result = extract_standard_metadata.invoke({"file_path": record.virtual_path})
    assert result["status"] == "ok"
    assert result["aggregated_summary"]["标准号"] == "GB/T 5678-2026"
    assert result["virtual_output_path"].startswith("/workspace/output/metadata/json/")

