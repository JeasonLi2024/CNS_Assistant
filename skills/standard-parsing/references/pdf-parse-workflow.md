# PDF 解析工作流

1. 用户上传文件应先由应用层保存到 `/workspace/input/uploads/{thread_id}/`。
2. PDF 或 Word 输入调用 `parse_file_with_mineru`（`return_images` 默认开启时按 `content_list` 重命名图片）。
3. Markdown 输入跳过解析。
4. 工具返回的 `virtual_md_path` 是后续元数据抽取的主要输入。
5. 工具返回的 `virtual_manifest_path` 与 `cover_metadata` 是下游定位产物与封面 hint 的主要入口。
6. **local**：配置 `MINERU_API_MODE=local` + `MINERU_API_BASE_URL`；**precise**：`MINERU_API_MODE=precise` + `MINERU_API_TOKEN`（无 `_middle.json` 时用 `layout.json`）。
