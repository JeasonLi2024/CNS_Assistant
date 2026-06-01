---
name: standard-extraction
description: 当用户要求从 Markdown 标准文档或 MinerU 解析结果中抽取国标元数据字段、生成结构化 JSON 和 manifest 时使用。
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
8. 如果输入仍是 PDF 或 Word，先请求主编排或 parser 调用 `parse_file_with_mineru` 转为 Markdown。

## 输出

工具会写入：

- `/workspace/output/metadata/json/*_metadata.json`（聚合结果，主产物）
- `/workspace/output/metadata/annotated/*_extraction.jsonl`
- `/workspace/output/metadata/normalized/*_extraction.json`
- `/workspace/output/metadata/manifests/*_manifest.json`

`extract_standard_metadata` 返回 `download.host_path`（本地可直接打开）及可选 `download.download_url`（需配置 `STANDARD_DOC_ARTIFACT_API_BASE`）。
