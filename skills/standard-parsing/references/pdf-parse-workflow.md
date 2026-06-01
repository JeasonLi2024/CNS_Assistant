# PDF 解析工作流

1. 用户上传文件应先由应用层保存到 `/workspace/input/uploads/{thread_id}/`。
2. PDF 输入调用 `parse_pdf_with_mineru`。
3. Markdown 输入跳过解析。
4. 工具返回的 `virtual_md_path` 是后续元数据抽取的主要输入。
5. 工具返回的 `virtual_manifest_path` 是下游定位所有产物的主要入口。

