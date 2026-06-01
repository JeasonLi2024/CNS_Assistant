"""Langextract adapter aligned with pending_tools/extract_from_md_new.py."""

from __future__ import annotations

import json
import os
import re
import textwrap
from pathlib import Path
from typing import Any

import langextract as lx
from langextract.providers.openai import OpenAILanguageModel

from standard_document_assistant.config import MetadataExtractionConfig, load_config
from standard_document_assistant.graphs.metadata_extraction.prompts import (
    EXTRACTION_PROMPT,
    MULTI_VALUE_CLASSES,
    SINGLE_VALUE_CLASSES,
    TARGET_CLASSES,
)


def slice_metadata_scope(text: str, scope_mode: str) -> str:
    """按 scope_mode 截取文本范围。

    - full: 使用全文。
    - metadata: 截取到第 4 章之前，聚焦封面、前言和第 2、3 章元信息。
    """
    if scope_mode == "full":
        return text

    match = re.search(
        r"(?im)^\s*#{1,6}\s*(?:第\s*4\s*章|4)(?!\s*[\.．]\s*\d)\s*(?:[、.．:：]\s*)?\S.*$",
        text,
    )
    if not match:
        return text
    scoped = text[: match.start()].strip()
    return scoped or text


def build_examples() -> list[Any]:
    return [
        lx.data.ExampleData(
            text=textwrap.dedent(
                """
                标准正式编号: GB/T 12345-2026
                代替GB/T 12345-2020
                ICS 12.300
                A 12
                文件代号: GB
                文件的层次或类别: 中华人民共和国国家标准
                发布机构: 中国国家标准化管理委员会

                # 智能体与检索增强生成系统规范
                # SpecificationforAgentandRetrieval-AugmentedGenerationSystem
                (ISO 6660:1993，MOD)
                2026-03-01 发布
                2026-10-01 实施


                # 前言
                本标准由中华人民共和国工业和信息化部提出。
                本标准由中国人工智能标准化协会归口。
                本标准起草单位: AI大模型工程院、某某科技。
                本标准主要起草人: 张三、李四。

                # 2 规范性引用文件
                下列文件中的内容通过文中的规范性引用而构成本文件必不可少的条款。
                GB/T11111 向量检索基础

                # 3 术语和定义
                下列术语和定义适用于本文件。
                混合检索 hybrid search
                """
            ).strip(),
            extractions=[
                lx.data.Extraction(extraction_class="ICS", extraction_text="12.300", attributes=None),
                lx.data.Extraction(extraction_class="CCS", extraction_text="A12", attributes=None),
                lx.data.Extraction(
                    extraction_class="标准层级",
                    extraction_text="中华人民共和国国家标准",
                    attributes=None,
                ),
                lx.data.Extraction(extraction_class="标准号", extraction_text="GB/T 12345-2026", attributes=None),
                lx.data.Extraction(
                    extraction_class="代替标准号",
                    extraction_text="GB/T 12345-2020",
                    attributes=None,
                ),
                lx.data.Extraction(extraction_class="发布日期", extraction_text="2026-03-01", attributes=None),
                lx.data.Extraction(extraction_class="实施日期", extraction_text="2026-10-01", attributes=None),
                lx.data.Extraction(
                    extraction_class="标准中文名称",
                    extraction_text="智能体与检索增强生成系统规范",
                    attributes=None,
                ),
                lx.data.Extraction(
                    extraction_class="标准英文名称",
                    extraction_text="Specification for Agent and Retrieval-Augmented Generation System",
                    attributes=None,
                ),
                lx.data.Extraction(extraction_class="采标信息", extraction_text="ISO 6660:1993，MOD", attributes=None),
                lx.data.Extraction(
                    extraction_class="提出单位",
                    extraction_text="中华人民共和国工业和信息化部",
                    attributes=None,
                ),
                lx.data.Extraction(
                    extraction_class="归口单位",
                    extraction_text="中国人工智能标准化协会",
                    attributes=None,
                ),
                lx.data.Extraction(extraction_class="起草单位", extraction_text="AI大模型工程院", attributes=None),
                lx.data.Extraction(extraction_class="起草单位", extraction_text="某某科技", attributes=None),
                lx.data.Extraction(extraction_class="起草人", extraction_text="张三", attributes=None),
                lx.data.Extraction(extraction_class="起草人", extraction_text="李四", attributes=None),
                lx.data.Extraction(extraction_class="引用文件", extraction_text="GB/T 11111 向量检索基础", attributes=None),
                lx.data.Extraction(extraction_class="专业术语", extraction_text="混合检索 hybrid search", attributes=None),
            ],
        )
    ]


def infer_standard_nature(file_number: str) -> str:
    code = (file_number or "").strip().upper()
    if not code:
        return ""

    compact = re.sub(r"\s+", " ", code)
    if re.match(r"^[A-Z]{2}/Z", compact):
        return "指导性技术文件"
    if re.match(r"^[A-Z]{2}/T", compact):
        return "推荐性"
    if compact.startswith("GA"):
        return "推荐性"
    if compact.startswith("GB"):
        return "强制性"
    return ""


def build_extraction_result(result: Any, source_name: str) -> dict[str, Any]:
    """固定输出 schema：单值字段用字符串，多值字段用去重列表。"""
    aggregated: dict[str, Any] = {key: "" for key in SINGLE_VALUE_CLASSES}
    for key in MULTI_VALUE_CLASSES:
        aggregated[key] = []
    aggregated["标准性质"] = ""
    aggregated["制修订"] = ""

    integrated: dict[str, dict[str, dict[str, Any]]] = {key: {} for key in TARGET_CLASSES}

    for item in result.extractions:
        cls = getattr(item, "extraction_class", None)
        txt = (getattr(item, "extraction_text", "") or "").strip()
        if not isinstance(cls, str):
            continue
        if not txt or cls not in integrated:
            continue

        char_interval = getattr(item, "char_interval", None)
        start_pos = getattr(char_interval, "start_pos", None) if char_interval else None

        existing = integrated[cls].get(txt)
        if not existing:
            integrated[cls][txt] = {"text": txt, "start_pos": start_pos}
        else:
            old_start = existing["start_pos"]
            if old_start is None or (start_pos is not None and start_pos < old_start):
                existing["start_pos"] = start_pos

    for cls in SINGLE_VALUE_CLASSES:
        candidates = list(integrated[cls].values())
        if not candidates:
            continue
        candidates.sort(
            key=lambda item: (
                item["start_pos"] is None,
                item["start_pos"] if item["start_pos"] is not None else float("inf"),
            )
        )
        aggregated[cls] = candidates[0]["text"]

    for cls in MULTI_VALUE_CLASSES:
        candidates = list(integrated[cls].values())
        candidates.sort(
            key=lambda item: (
                item["start_pos"] is None,
                item["start_pos"] if item["start_pos"] is not None else float("inf"),
                item["text"],
            )
        )
        aggregated[cls] = [item["text"] for item in candidates]

    aggregated["ics"] = aggregated.pop("ICS", "")
    aggregated["ccs"] = aggregated.pop("CCS", "")
    aggregated["标准性质"] = infer_standard_nature(str(aggregated.get("标准号", "")))
    aggregated["制修订"] = "修订" if str(aggregated.get("代替标准号", "")).strip() else "制订"
    aggregated["源文件"] = source_name
    return aggregated


def build_model(config: MetadataExtractionConfig | None = None) -> OpenAILanguageModel:
    cfg = config or load_config().metadata_extraction
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("缺少 DASHSCOPE_API_KEY，无法调用 langextract 元数据抽取模型。")
    model_id = os.getenv("METADATA_MODEL_ID") or os.getenv("MODEL_ID") or cfg.model
    base_url = os.getenv("DASHSCOPE_BASE_URL") or cfg.base_url or None
    return OpenAILanguageModel(
        model_id=model_id,
        base_url=base_url,
        api_key=api_key,
    )


def run_extraction(text: str, *, config: MetadataExtractionConfig | None = None) -> Any:
    cfg = config or load_config().metadata_extraction
    model = build_model(cfg)
    return lx.extract(
        text_or_documents=text,
        prompt_description=EXTRACTION_PROMPT,
        examples=build_examples(),
        model=model,
        batch_length=cfg.batch_length,
        max_workers=cfg.max_workers,
        max_char_buffer=cfg.max_char_buffer,
        extraction_passes=cfg.extraction_passes,
    )


def jsonl_to_structured_json(jsonl_path: Path) -> Any:
    records: list[Any] = []
    with jsonl_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records[0] if len(records) == 1 else records


def save_langextract_outputs(
    *,
    result: Any,
    annotated_dir: Path,
    normalized_dir: Path,
    output_stem: str,
) -> dict[str, Path]:
    """写入 annotated jsonl 与 normalized json，并返回路径映射。"""
    annotated_dir.mkdir(parents=True, exist_ok=True)
    normalized_dir.mkdir(parents=True, exist_ok=True)
    annotated_name = f"{output_stem}_extraction.jsonl"
    normalized_name = f"{output_stem}_extraction.json"

    lx.io.save_annotated_documents(
        iter([result]),
        output_name=annotated_name,
        output_dir=str(annotated_dir),
    )
    annotated_path = annotated_dir / annotated_name
    normalized = jsonl_to_structured_json(annotated_path)
    normalized_path = normalized_dir / normalized_name
    normalized_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "annotated": annotated_path,
        "normalized": normalized_path,
    }


def collect_quality_warnings(aggregated: dict[str, Any], *, hint: dict[str, Any] | None = None) -> list[str]:
    warnings: list[str] = []
    standard_number = str(aggregated.get("标准号", "")).strip().upper()
    level = str(aggregated.get("标准层级", "")).strip()

    if not standard_number:
        warnings.append("未抽取到标准号，请人工核对源文档封面或前言。")
    if standard_number.startswith("GH/T") and "国家标准" in level:
        warnings.append("标准号为 GH/T 但标准层级含“国家标准”，请人工核对。")
    if standard_number.startswith("GB") and level and "国家标准" not in level:
        warnings.append("标准号为 GB 系列但标准层级未识别为国家标准，请人工核对。")

    if hint:
        for field, hint_key in [
            ("标准号", "standard_number"),
            ("ics", "ics"),
            ("ccs", "ccs"),
            ("标准层级", "hierarchy_or_category"),
        ]:
            hint_value = str(hint.get(hint_key, "") or "").strip()
            actual = str(aggregated.get(field, "") or "").strip()
            if hint_value and actual and hint_value != actual:
                warnings.append(f"cover_metadata_hint.{hint_key}={hint_value!r} 与抽取结果 {actual!r} 不一致，未自动修改 JSON。")

    return warnings
