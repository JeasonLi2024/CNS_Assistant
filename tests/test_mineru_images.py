import io
import json
import zipfile
from pathlib import Path

from standard_document_assistant.integrations.mineru.images import (
    build_content_list_name_suggestions,
    collect_zip_image_entries,
    is_image_zip_entry,
    persist_renamed_images,
    relative_image_ref_prefix,
    rewrite_markdown_image_refs,
)
from standard_document_assistant.integrations.mineru.naming import prepend_cover_info
from standard_document_assistant.integrations.mineru.zip_parser import parse_result_zip
from standard_document_assistant.pathing import mineru_output_root


def test_build_content_list_name_suggestions_for_figure_and_subfigure() -> None:
    content_list = [
        {"type": "text", "text": "结果见图"},
        {
            "type": "image",
            "img_path": "images/aaa111.jpg",
            "image_caption": ["a）"],
        },
        {
            "type": "image",
            "img_path": "images/bbb222.jpg",
            "image_caption": ["图1 试验装置"],
        },
        {
            "type": "table",
            "img_path": "images/ccc333.jpg",
            "table_caption": ["表1 参数"],
            "table_body": "<table><tr><td>x</td></tr></table>",
        },
    ]
    md = "# 正文\n\n表1 参数\n\n<table><tr><td>x</td></tr></table>\n\n![](images/bbb222.jpg)\n"
    suggestions = build_content_list_name_suggestions(content_list, md)
    assert suggestions["bbb222.jpg"] == "图1 试验装置"
    assert suggestions["ccc333.jpg"] == "表1 参数"
    assert suggestions["aaa111.jpg"] == "结果见图a）"


def test_is_image_zip_entry_supports_local_and_precise_layouts() -> None:
    assert is_image_zip_entry("doc/sample/images/hash.jpg")
    assert is_image_zip_entry("images/hash.jpg")
    assert not is_image_zip_entry("doc/sample/sample.md")


def test_rewrite_markdown_image_refs_replaces_images_prefix_and_markdown_links() -> None:
    md = "正文 ![](images/abc123def456789012345678901234567890.jpg) 结束"
    updated = rewrite_markdown_image_refs(
        md,
        {"abc123def456789012345678901234567890.jpg": "图1-试验.jpg"},
        rel_image_prefix="../../images/GB-T-1-2026",
    )
    assert "../../images/GB-T-1-2026/图1-试验.jpg" in updated
    assert "images/abc123def456789012345678901234567890.jpg" not in updated


def test_prepend_cover_info_uses_mineru_field_labels() -> None:
    md = prepend_cover_info(
        "# 正文",
        {
            "standard_number": "GB/T 1-2026",
            "replaced_standard_number": "GB/T 1-2020",
            "ics": "ICS 01.020",
            "ccs": "B 31",
            "file_code": "GB",
            "hierarchy_or_category": "国家标准",
            "issuing_organizations": "国家市场监督管理总局",
        },
    )
    assert "标准正式编号：GB/T 1-2026" in md
    assert "代替GB/T 1-2020" in md
    assert "文件代号：GB" in md
    assert "文件的层次或类别：国家标准" in md
    assert "发布机构：国家市场监督管理总局" in md


def test_parse_result_zip_renames_images_from_content_list() -> None:
    content_list = [
        {
            "type": "image",
            "img_path": "images/deadbeefdeadbeefdeadbeefdeadbeef.jpg",
            "image_caption": ["图2 流程图"],
        }
    ]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("full.md", "见图 ![](images/deadbeefdeadbeefdeadbeefdeadbeef.jpg)")
        archive.writestr("layout.json", json.dumps({"pdf_info": []}))
        archive.writestr(
            "sample_content_list.json",
            json.dumps(content_list, ensure_ascii=False),
        )
        archive.writestr("images/deadbeefdeadbeefdeadbeefdeadbeef.jpg", b"fake-jpeg-bytes")

    result = parse_result_zip(
        zip_bytes=buffer.getvalue(),
        source_stem="image-sample",
        output_root=mineru_output_root("pytest-images"),
        return_images=True,
        save_middle_json=False,
        save_content_list=False,
    )
    md_text = result["md_path"].read_text(encoding="utf-8")
    image_dir = result["image_root"]
    assert image_dir is not None
    saved = list(image_dir.glob("*.jpg"))
    assert saved
    assert saved[0].name.startswith("图2")
    assert "图2" in md_text
    assert "deadbeefdeadbeefdeadbeefdeadbeef.jpg" not in md_text


def test_parse_result_zip_local_nested_image_paths() -> None:
    hash_name = "cafebabecafebabecafebabecafebabe.jpg"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("doc/sample.md", f"![](images/{hash_name})")
        archive.writestr(f"doc/images/{hash_name}", b"nested-image")
        archive.writestr(
            "doc/sample_content_list.json",
            json.dumps(
                [
                    {
                        "type": "image",
                        "img_path": f"images/{hash_name}",
                        "image_caption": ["图3 局部图"],
                    }
                ]
            ),
        )

    result = parse_result_zip(
        zip_bytes=buffer.getvalue(),
        source_stem="nested",
        output_root=mineru_output_root("pytest-nested"),
        return_images=True,
        save_middle_json=False,
        save_content_list=False,
    )
    entries = collect_zip_image_entries([f"doc/images/{hash_name}"])
    assert entries
    assert result["image_root"] is not None
    assert list(result["image_root"].glob("图3*.jpg"))


def test_relative_image_ref_prefix_from_category_md_dir() -> None:
    root = mineru_output_root("pytest-relpath")
    md_parent = root / "md" / "国家标准"
    image_root = root / "images"
    image_subdir = image_root / "GB-T-1-2026"
    md_parent.mkdir(parents=True, exist_ok=True)
    prefix = relative_image_ref_prefix(
        md_parent=md_parent,
        image_root=image_root,
        image_subdir=image_subdir,
    )
    assert prefix.replace("\\", "/") == "../../images/GB-T-1-2026"
