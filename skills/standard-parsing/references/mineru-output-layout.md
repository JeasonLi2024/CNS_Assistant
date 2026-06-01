# MinerU 输出目录

```text
/workspace/output/mineru/
├── zip/{source_stem}.zip
├── md/
│   ├── 国家标准/
│   ├── 行业标准/
│   ├── 地方标准/
│   └── 其他/
├── images/{标准号}/     # 按 content_list 语义重命名
├── json/                # 可选：middle_*.json / layout_*.json、content_list_*.json
└── manifests/*_parse_manifest.json
```

## ZIP 内常见结构

**local（自建服务）**

```text
{pdf_name}/{parse_dir}/{name}.md
{pdf_name}/{parse_dir}/{name}_middle.json
{pdf_name}/{parse_dir}/images/{sha256}.jpg
{pdf_name}/{parse_dir}/*_content_list.json
```

**precise（API）**

```text
full.md
layout.json
*_content_list.json
images/{sha256}.jpg
```

解析器对两种布局统一处理：识别 `images/` 路径（含嵌套目录）、`layout.json` 封面块、`content_list` 图题命名。

不要从对话中复制大段 Markdown；用 `read_file` 读取 `virtual_md_path`。
