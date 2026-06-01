from __future__ import annotations

from types import SimpleNamespace

import pytest

from standard_document_assistant.graphs.metadata_extraction.langextract_runner import (
    build_extraction_result,
    collect_quality_warnings,
    slice_metadata_scope,
)
from standard_document_assistant.tools import extract_standard_metadata
from standard_document_assistant.uploads import save_uploaded_file


class _Interval:
    def __init__(self, start_pos: int) -> None:
        self.start_pos = start_pos


def _mock_langextract_result() -> SimpleNamespace:
    extractions = [
        SimpleNamespace(
            extraction_class="ICS",
            extraction_text="03.120.99",
            char_interval=_Interval(10),
        ),
        SimpleNamespace(
            extraction_class="CCS",
            extraction_text="A00",
            char_interval=_Interval(20),
        ),
        SimpleNamespace(
            extraction_class="标准号",
            extraction_text="GB/T 5678-2026",
            char_interval=_Interval(30),
        ),
        SimpleNamespace(
            extraction_class="标准中文名称",
            extraction_text="元数据测试标准",
            char_interval=_Interval(40),
        ),
        SimpleNamespace(
            extraction_class="引用文件",
            extraction_text="GB/T 1.1",
            char_interval=_Interval(50),
        ),
    ]
    return SimpleNamespace(extractions=extractions)


def test_slice_metadata_scope_stops_before_chapter_four() -> None:
    text = "# 封面\n\n## 3 术语\n\n术语A\n\n## 4 范围\n\n正文"
    scoped = slice_metadata_scope(text, "metadata")
    assert "术语A" in scoped
    assert "正文" not in scoped


def test_build_extraction_result_maps_schema_fields() -> None:
    aggregated = build_extraction_result(_mock_langextract_result(), "/workspace/input/test.md")
    assert aggregated["标准号"] == "GB/T 5678-2026"
    assert aggregated["ics"] == "03.120.99"
    assert aggregated["ccs"] == "A00"
    assert aggregated["制修订"] == "制订"


def test_collect_quality_warnings_flags_gh_t_national_level() -> None:
    warnings = collect_quality_warnings(
        {"标准号": "GH/T 1513-2025", "标准层级": "中华人民共和国国家标准"},
    )
    assert any("GH/T" in item for item in warnings)


def test_extract_standard_metadata_from_uploaded_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert result["aggregated"]["标准号"] == "GB/T 5678-2026"
    assert result["virtual_output_path"].startswith("/workspace/output/metadata/json/")
    assert result["virtual_manifest_path"].startswith("/workspace/output/metadata/manifests/")
    assert result["virtual_annotated_path"].startswith("/workspace/output/metadata/annotated/")
    assert result["virtual_normalized_path"].startswith("/workspace/output/metadata/normalized/")
    assert result["download"]["host_path"]
    assert result["scoped_text_chars"] > 0
