import io
import json
import zipfile
from pathlib import Path

from standard_document_assistant.integrations.mineru.naming import (
    _extract_cover_metadata_from_pdf_info,
    extract_cover_metadata,
    has_pdf_info_payload,
)
from standard_document_assistant.integrations.mineru.zip_parser import parse_result_zip
from standard_document_assistant.pathing import mineru_output_root


def _page0_header_block(index: int, text: str) -> dict:
    return {
        "type": "header",
        "index": index,
        "lines": [{"spans": [{"content": text}]}],
    }


def test_extract_cover_metadata_from_pdf_info_distinguishes_replaced_standard() -> None:
    layout_json = {
        "pdf_info": [
            {
                "page_idx": 0,
                "discarded_blocks": [
                    _page0_header_block(0, "ICS 67.080.10"),
                    _page0_header_block(1, "B 31"),
                    _page0_header_block(2, "GB"),
                    _page0_header_block(3, "中华人民共和国国家标准"),
                    _page0_header_block(4, "GB/T 15034—2009"),
                    _page0_header_block(5, "代替 GB/T 15034—1994"),
                ],
            }
        ]
    }
    metadata = _extract_cover_metadata_from_pdf_info(layout_json)
    assert metadata["standard_number"] == "GB/T 15034-2009"
    assert metadata["replaced_standard_number"] == "GB/T 15034-1994"
    assert metadata["file_code"] == "GB"
    assert metadata["hierarchy_or_category"] == "国家标准"


def test_extract_cover_metadata_text_fallback_skips_replaced_line() -> None:
    markdown = (
        "# 芒果 贮藏导则\n\n"
        "本标准代替 GB/T 15034—1994《芒果 贮藏导则》。\n"
        "本标准与 GB/T 15034—1994 相比主要差异如下：\n"
    )
    metadata = extract_cover_metadata({}, markdown)
    assert metadata["standard_number"] == ""
    assert metadata["replaced_standard_number"] == "GB/T 15034-1994"


def test_parse_result_zip_uses_layout_json_when_middle_json_missing() -> None:
    zip_path = Path("workspace/output/mineru/zip/GBT15034-2009.zip")
    if not zip_path.exists():
        return

    result = parse_result_zip(
        zip_bytes=zip_path.read_bytes(),
        source_stem="GBT15034-2009",
        output_root=mineru_output_root("pytest-cover"),
        return_images=False,
        save_middle_json=False,
        save_content_list=False,
    )
    cover = result["cover_metadata"]
    assert cover["standard_number"] == "GB/T 15034-2009"
    assert cover["replaced_standard_number"] == "GB/T 15034-1994"
    assert result["md_category"] == "国家标准"
    assert "GB-T-15034-2009" in result["md_path"].name


def test_parse_result_zip_layout_only_mock() -> None:
    layout = {
        "pdf_info": [
            {
                "page_idx": 0,
                "discarded_blocks": [
                    _page0_header_block(4, "GB/T 9999—2026"),
                ],
            }
        ]
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("full.md", "# 测试\n\n本标准代替 GB/T 8888—2020。")
        archive.writestr("layout.json", json.dumps(layout, ensure_ascii=False))

    result = parse_result_zip(
        zip_bytes=buffer.getvalue(),
        source_stem="layout-only",
        output_root=mineru_output_root("pytest-layout"),
        return_images=False,
        save_middle_json=False,
        save_content_list=False,
    )
    assert result["cover_metadata"]["standard_number"] == "GB/T 9999-2026"
    assert has_pdf_info_payload(layout)
