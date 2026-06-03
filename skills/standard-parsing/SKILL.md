---
name: standard-parsing
description: 仅在输入是 /workspace/input/uploads/** 或 /workspace/input/samples/** 下后缀为 .pdf 或 .docx 的标准文档时使用；调用 parse_file_with_mineru 还原版式、抽取封面元信息并落盘到 /workspace/output/mineru。已有 Markdown / content_list 时不要再次解析。
---

# Standard Parsing

## 何时使用

1. 输入是 `/workspace/input/uploads/**` 或 `/workspace/input/samples/**` 下的 PDF 或 Word 标准文档。
2. 用户要求“解析 PDF/Word”“转换为 Markdown”“抽取前先解析文档”“审核前先解析文档”。
3. 需要保留版式、图片、表格或封面信息。

输入已是 Markdown、txt 或已经位于 `/workspace/output/mineru/**/*.md` 时，不调用 MinerU，直接把路径交给后续 `extractor` 或 `reviewer`。

## MinerU 调用模式

| 模式 | 环境变量 | 说明 |
|------|----------|------|
| **local**（与 `minerU2_2.py` 一致） | `MINERU_API_MODE=local`、`MINERU_API_BASE_URL` | 自建服务 `POST /file_parse`，一次返回 ZIP；ZIP 内常见 `{pdf}/{dir}/images/*.jpg`、`*_middle.json` |
| **precise** | `MINERU_API_MODE=precise`、`MINERU_API_TOKEN` | mineru.net 上传→轮询→下载 ZIP；常无 `_middle.json`，但有 `layout.json`、`content_list.json` |

封面元信息抽取顺序：`middle_json` → `layout.json`（第 0 页 `discarded_blocks`）→ `content_list` / Markdown 文本回退。  
图片命名：读取 ZIP 内 `content_list`，按「图 x / 表 x」题注或子图 `a）` 规则重命名，并改写 MD 中 `images/{hash}.jpg` 引用。

## 工作流

1. 确认输入路径是 `/workspace/` 虚拟路径，不能使用 Windows 盘符路径。
2. 调用 `parse_file_with_mineru(file_path=...)`（可选 `skip_if_zip_exists=True` 复用已存 ZIP）。
3. 解析成功后只返回摘要、`virtual_md_path`、`virtual_manifest_path`、`cover_metadata`、`warnings`。
4. 不粘贴 Markdown 全文。
5. 需要元数据时，委派 `extractor` 调用 `extract_standard_metadata`，传入 `virtual_md_path` 与 `cover_metadata_hint`（与 MD 头部字段一致）。
6. 下一步是标准审核时，直接把 `virtual_md_path` 交给 `reviewer`。

## cover_metadata 字段

| 字段 | 含义 |
|------|------|
| `standard_number` | 标准正式编号（非“代替”行） |
| `replaced_standard_number` | 被代替标准号 |
| `ics` / `ccs` | 分类号 |
| `file_code` | 文件代号（如 `GB`） |
| `hierarchy_or_category` | 国家标准 / 行业标准 / 地方标准 |
| `issuing_organizations` | 发布机构 |

写入 MD 头部的标签与 `minerU2_2.py` 对齐（含「文件代号」「文件的层次或类别」「发布机构」）。

## 产物

```text
/workspace/output/mineru/
├── zip/           # 原始 ZIP（可断点续跑）
├── md/{国家标准|行业标准|地方标准|其他}/
├── images/{标准号}/
├── json/          # 可选 middle/layout、content_list
└── manifests/
```

优先把 `virtual_manifest_path` 交给下游，避免猜测文件位置。

## 失败处理

见 `references/mineru-failures.md`。
