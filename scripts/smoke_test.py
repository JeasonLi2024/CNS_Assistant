"""标准文档助手项目骨架的冒烟测试脚本。

作用
----
不依赖 pytest，单独可跑。验证主 Agent 的最小可用骨架没有被人改坏：

1. **项目目录结构** —— 调用 `managed_project_shape()`，断言返回的每个目录
   路径在磁盘上真实存在。
2. **主工具注册表** —— 断言 `STANDARD_DOCUMENT_TOOLS` 恰好只包含
   `validate_output_schema` 和 `propose_memory_update` 两个工具。
3. **subagent 列表** —— 断言 `build_subagents()` 返回的 subagent 名称集合中
   **不包含** `vision_parser`。
4. **元数据抽取端到端** —— 走一次
   `save_uploaded_file` → `extract_standard_metadata` → `validate_output_schema`
   的完整链路，断言：
   - 上传成功；
   - 元数据 `status == "ok"`，且 `aggregated_summary["标准号"] == "GB/T 9999-2026"`；
   - 返回字段 `download.host_path` / `virtual_output_path` /
     `virtual_manifest_path` 均非空。

运行方式
-------
在项目根目录 `d:\\deep-agents\\` 下执行：

```powershell
# 直接跑（不需要 pytest）
python scripts/smoke_test.py

# 走 pytest 自动发现（tests/test_smoke_tools.py 已经做了 1 行薄包装）
pytest tests/test_smoke_tools.py -v
```

环境与副作用
------------
- 默认会 monkey-patch `standard_document_assistant.graphs.metadata_extraction.nodes`
  里的 `_traced_run_extraction` 和 `save_langextract_outputs`，用 mock 结果替代
  真实 langextract 调用；因此**无需 `DASHSCOPE_API_KEY`** 也能跑通。
  当环境里已设置 `DASHSCOPE_API_KEY` 时，自动跳过 patch，走真实模型。
- 自动设置 `LANGSMITH_TRACING=false` / `LANGCHAIN_TRACING_V2=false`，
  避免误触外网。
- 会向 `uploads/smoke_standard.md` 写入一个临时样本（运行后可删除）。

输出
----
成功时打印一段 JSON：
```json
{
  "status": "ok",
  "sample": "<virtual_path>",
  "metadata": "<virtual_output_path>",
  "manifest": "<virtual_manifest_path>",
  "output_dir": "<OUTPUT_DIR>"
}
```
失败时抛 `AssertionError`，并在消息中指出具体哪一条断言没通过。
"""

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
