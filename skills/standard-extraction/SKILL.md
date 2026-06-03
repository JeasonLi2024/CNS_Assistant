---
name: standard-extraction
description: |
  Use ONLY for 国标 Markdown 元数据抽取：输入必须为 Markdown 虚拟路径
  （/workspace/output/mineru/**/*.md 或 /workspace/input/uploads/**/*.md），
  通过 extract_standard_metadata 工具（内部 langextract + 子图）抽取 16 类字段
  （ICS/CCS/标准号/标准层级/提出/归口/起草/引用/术语 等），并落盘 JSON / annotated /
  normalized / manifest。强约束：不要 read_file 预读全文；不要 edit_file 改写
  元数据 JSON；PDF/Word 必须先委派 parser 调用 parse_file_with_mineru。返回值
  通过 aggregated_summary + quality_warnings + download 字段汇报给主 Agent，
  不要再二次校验。
---

# Standard Extraction

## 何时使用

1. 输入是 `/workspace/output/mineru/**/*.md`、`/workspace/input/uploads/**/*.md` 或 Markdown 正文。
2. 用户要求抽取标准号、ICS、CCS、标准名称、发布日期、实施日期、提出单位、归口单位、起草单位、引用文件、术语等元数据。
3. PDF 或 Word 已由 parser 解析完成，并提供了 `virtual_md_path`。

## Instructions

1. 正式元数据抽取必须调用 `extract_standard_metadata`（子图内使用 langextract，含切分、LLM 调用、聚合与落盘）。
2. 默认 `scope_mode="metadata"`（截取至第 4 章前）；封面或前言字段缺失时可改用 `scope_mode="full"`。
3. 如果 parser 返回了 `cover_metadata`，作为 `cover_metadata_hint` 传入工具；hint 与抽取结果冲突时只产生 `quality_warnings`，不会自动改 JSON。
4. 工具返回 `aggregated` 与 `quality_warnings` 后，不要用 `read_file`/`edit_file` 打开或修改 JSON；疑似错误交给用户判断。
5. 大段原文不要直接返回给主 Agent；只返回字段摘要、JSON 路径、manifest 路径和 `download` 信息。
6. 收到 Markdown 或 MinerU Markdown 路径时，不要先用 `read_file` 预读全文，直接调用 `extract_standard_metadata(file_path=...)`。
7. 不要为了常规抽取读取本 skill；只有用户明确要求解释字段定义时再读取引用材料。
8. 如果输入仍是 PDF 或 Word，由主 Agent 先委派 parser 调用 `parse_file_with_mineru`；本代理仅对返回的 `virtual_md_path` 调用 `extract_standard_metadata`，并传入 `cover_metadata_hint`（来自 parser 的 `cover_metadata`）。

## 输出

工具会写入：

- `/workspace/output/metadata/json/*_metadata.json`（聚合结果，主产物）
- `/workspace/output/metadata/annotated/*_extraction.jsonl`
- `/workspace/output/metadata/normalized/*_extraction.json`
- `/workspace/output/metadata/manifests/*_manifest.json`

`extract_standard_metadata` 返回 `download.host_path`（本地可直接打开）及可选 `download.download_url`（需配置 `STANDARD_DOC_ARTIFACT_API_BASE`）。
