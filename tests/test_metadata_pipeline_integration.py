"""Integration: MinerU parse artifact -> metadata extraction read path."""

from __future__ import annotations

import io
import json
import zipfile
from types import SimpleNamespace

import pytest

from standard_document_assistant.graphs.metadata_extraction.langextract_runner import (
    slice_metadata_scope,
)
from standard_document_assistant.integrations.mineru.zip_parser import parse_result_zip
from standard_document_assistant.pathing import mineru_output_root
from standard_document_assistant.tools import extract_standard_metadata


class _Interval:
    def __init__(self, start_pos: int) -> None:
        self.start_pos = start_pos


def _mock_langextract_result() -> SimpleNamespace:
    return SimpleNamespace(
        extractions=[
            SimpleNamespace(
                extraction_class="标准号",
                extraction_text="GB/T 15034-2009",
                char_interval=_Interval(1),
            ),
            SimpleNamespace(
                extraction_class="标准中文名称",
                extraction_text="芒果 贮藏导则",
                char_interval=_Interval(2),
            ),
        ]
    )


def _layout_with_standard_number() -> dict:
    return {
        "pdf_info": [
            {
                "page_idx": 0,
                "discarded_blocks": [
                    {
                        "type": "header",
                        "index": 4,
                        "lines": [{"spans": [{"content": "GB/T 15034—2009"}]}],
                    },
                    {
                        "type": "header",
                        "index": 5,
                        "lines": [{"spans": [{"content": "代替 GB/T 15034—1994"}]}],
                    },
                ],
            }
        ]
    }


def test_mineru_parse_then_metadata_extraction_reads_virtual_md_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "full.md",
            (
                "标准正式编号：GB/T 15034-2009\n\n"
                "# 芒果 贮藏导则\n\n"
                "## 2 规范性引用文件\n\n"
                "GB/T 8210 出口柑桔鲜果检验方法\n\n"
                "## 4 范围\n\n"
                "本章之后不应进入 metadata 范围。\n"
            ),
        )
        archive.writestr("layout.json", json.dumps(_layout_with_standard_number()))

    parsed = parse_result_zip(
        zip_bytes=buffer.getvalue(),
        source_stem="pipeline-sample",
        output_root=mineru_output_root("pytest-pipeline"),
        return_images=False,
        save_middle_json=False,
        save_content_list=False,
    )
    virtual_md = parsed["md_path"]
    assert virtual_md.exists()
    cover = parsed["cover_metadata"]
    assert cover["standard_number"] == "GB/T 15034-2009"

    monkeypatch.setattr(
        "standard_document_assistant.graphs.metadata_extraction.nodes._traced_run_extraction",
        lambda _text: _mock_langextract_result(),
    )

    def _fake_save_langextract_outputs(
        *,
        result: object,
        annotated_dir,
        normalized_dir,
        output_stem: str,
    ) -> dict[str, object]:
        annotated_path = annotated_dir / f"{output_stem}_extraction.jsonl"
        normalized_path = normalized_dir / f"{output_stem}_extraction.json"
        annotated_path.write_text("{}\n", encoding="utf-8")
        normalized_path.write_text("{}", encoding="utf-8")
        return {"annotated": annotated_path, "normalized": normalized_path}

    monkeypatch.setattr(
        "standard_document_assistant.graphs.metadata_extraction.nodes.save_langextract_outputs",
        _fake_save_langextract_outputs,
    )

    from standard_document_assistant.pathing import host_to_virtual_path

    md_virtual = host_to_virtual_path(virtual_md)
    result = extract_standard_metadata.invoke(
        {
            "file_path": md_virtual,
            "cover_metadata_hint": cover,
            "scope_mode": "metadata",
        }
    )

    assert result["status"] == "ok"
    assert result["source_virtual_path"] == md_virtual
    assert result["aggregated"]["标准号"] == "GB/T 15034-2009"
    full_text = virtual_md.read_text(encoding="utf-8")
    scoped = slice_metadata_scope(full_text, "metadata")
    assert "本章之后不应进入 metadata 范围" not in scoped
    assert result["scoped_text_chars"] == len(scoped)
    assert result["virtual_output_path"].startswith("/workspace/output/metadata/json/")
