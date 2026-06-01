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

1. 正式元数据抽取必须调用 `extract_standard_metadata`。
2. 默认 `scope_mode="metadata"`；封面或前言字段缺失时可改用 `scope_mode="full"`。
3. 如果 parser 返回了 `cover_metadata`，作为 `cover_metadata_hint` 传入工具。
4. 保留不确定字段，不要臆测补全。
5. 大段原文不要直接返回给主 Agent；只返回字段摘要、JSON 路径和 manifest 路径。
6. 收到 Markdown 或 MinerU Markdown 路径时，不要先用 `read_file` 预读全文，直接调用 `extract_standard_metadata(file_path=...)`。
7. 不要为了常规抽取读取本 skill 或 `references/metadata-fields.md`；只有用户明确要求解释字段定义时再读取引用材料。
8. 如果输入仍是 PDF 或 Word，先请求主编排或 parser 调用 `parse_file_with_mineru` 转为 Markdown。

## 输出

工具会写入：

- `/workspace/output/metadata/json/*_metadata.json`
- `/workspace/output/metadata/manifests/*_manifest.json`

后续审核或起草优先读取 manifest，再按需读取 JSON。
