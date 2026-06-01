---
name: standard-parsing
description: 当用户上传 PDF 标准文档、需要版式还原或扫描件 OCR 解析为 Markdown 时使用；指导调用 parse_pdf_with_mineru 并返回产物路径。
---

# Standard Parsing

## 何时使用

1. 输入是 `/workspace/input/uploads/**` 或 `/workspace/input/samples/**` 下的 PDF 标准文档。
2. 用户要求“解析 PDF”“转换为 Markdown”“抽取前先解析文档”。
3. 需要保留版式、图片、表格或封面信息。

输入已是 Markdown、txt 或已经位于 `/workspace/output/mineru/**/*.md` 时，不调用 MinerU，直接把路径交给 `extractor`。

## 工作流

1. 确认输入路径是 `/workspace/` 虚拟路径，不能使用 Windows 盘符路径。
2. 调用 `parse_pdf_with_mineru(file_path=...)`。
3. 解析成功后只返回摘要、`virtual_md_path`、`virtual_manifest_path`、`cover_metadata`。
4. 不粘贴 Markdown 全文。
5. 如果还需要元数据，委派 `extractor` 调用 `extract_standard_metadata`，并传入 `virtual_md_path` 与 `cover_metadata_hint`。

## 产物

MinerU 产物位于：

- `/workspace/output/mineru/md/`
- `/workspace/output/mineru/images/`
- `/workspace/output/mineru/json/`
- `/workspace/output/mineru/zip/`
- `/workspace/output/mineru/manifests/`

优先把 manifest 路径交给后续步骤，避免后续步骤猜测文件位置。

## 失败处理

1. `MINERU_API_BASE_URL` 未配置：明确说明服务地址缺失。
2. 服务不可达或超时：明确说明 MinerU 调用失败，不伪造 Markdown。
3. ZIP 中无 Markdown：标记解析失败，并保留可用错误信息。

