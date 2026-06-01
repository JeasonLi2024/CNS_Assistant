"""Smoke tests for the standard document assistant project skeleton."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("LANGSMITH_TRACING", "false")
os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from standard_document_assistant.agent import managed_project_shape
from standard_document_assistant.agent import build_subagents
from standard_document_assistant.constants import OUTPUT_DIR
from standard_document_assistant.tools import (
    STANDARD_DOCUMENT_TOOLS,
    extract_standard_metadata,
    validate_output_schema,
)
from standard_document_assistant.uploads import save_uploaded_file


def _patch_langextract_for_smoke() -> None:
    if os.getenv("DASHSCOPE_API_KEY"):
        return
    from types import SimpleNamespace

    from standard_document_assistant.graphs.metadata_extraction import nodes as metadata_nodes

    def _mock_result() -> SimpleNamespace:
        return SimpleNamespace(
            extractions=[
                SimpleNamespace(
                    extraction_class="标准号",
                    extraction_text="GB/T 9999-2026",
                    char_interval=SimpleNamespace(start_pos=1),
                ),
                SimpleNamespace(
                    extraction_class="标准中文名称",
                    extraction_text="数据质量管理规范",
                    char_interval=SimpleNamespace(start_pos=2),
                ),
            ]
        )

    def _fake_save(**kwargs):
        annotated_dir = kwargs["annotated_dir"]
        normalized_dir = kwargs["normalized_dir"]
        output_stem = kwargs["output_stem"]
        annotated = annotated_dir / f"{output_stem}_extraction.jsonl"
        normalized = normalized_dir / f"{output_stem}_extraction.json"
        annotated.write_text("{}\n", encoding="utf-8")
        normalized.write_text("{}", encoding="utf-8")
        return {"annotated": annotated, "normalized": normalized}

    metadata_nodes._traced_run_extraction = lambda _text: _mock_result()
    metadata_nodes.save_langextract_outputs = _fake_save


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    _patch_langextract_for_smoke()
    record = save_uploaded_file(
        original_filename="sample_standard.md",
        thread_id="smoke",
        content="""标准正式编号：GB/T 9999-2026
ICS 03.120.99
CCS A00

# 数据质量管理规范

## 1 范围

本文件规定了数据质量管理的对象、流程和评价要求。

## 2 规范性引用文件

- GB/T 1.1

## 3 术语和定义

数据质量：数据满足业务使用要求的程度。

## 4 技术要求

TODO：补充质量评价指标。
""".encode("utf-8"),
        content_type="text/markdown",
    )

    extracted = extract_standard_metadata.invoke({"file_path": record.virtual_path})
    assert_true(extracted["status"] == "ok", "元数据抽取应成功")
    assert_true(extracted["aggregated_summary"]["标准号"] == "GB/T 9999-2026", "应抽取标准号")
    assert_true(extracted.get("download", {}).get("host_path"), "应返回本地下载路径")
    assert_true(extracted["virtual_output_path"], "应返回元数据 JSON 虚拟路径")
    assert_true(extracted["virtual_manifest_path"], "应返回 manifest 虚拟路径")

    validation = validate_output_schema(
        {
            "summary": "smoke ok",
            "task_type": "extract",
            "artifacts": [
                {
                    "path": extracted["virtual_output_path"],
                    "type": "extracted_json",
                    "description": "smoke metadata",
                }
            ],
            "findings": [],
            "next_steps": [],
        }
    )
    assert_true(validation["valid"], "AgentResult schema 应校验通过")

    shape = managed_project_shape()
    for key, value in shape.items():
        assert_true(Path(value).exists(), f"Managed project shape missing: {key} -> {value}")
    assert_true(
        {tool.__name__ for tool in STANDARD_DOCUMENT_TOOLS}
        == {"validate_output_schema", "propose_memory_update"},
        "主工具注册表应只包含 schema 校验和记忆提案工具",
    )
    assert_true(
        "vision_parser" not in {agent["name"] for agent in build_subagents()},
        "subagents 不应包含 vision_parser",
    )

    print(
        json.dumps(
            {
                "status": "ok",
                "sample": record.virtual_path,
                "metadata": extracted["virtual_output_path"],
                "manifest": extracted["virtual_manifest_path"],
                "output_dir": str(OUTPUT_DIR),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
