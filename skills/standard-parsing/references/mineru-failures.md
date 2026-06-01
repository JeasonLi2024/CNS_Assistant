# MinerU 常见失败

## 配置

| 现象 | 处理 |
|------|------|
| local 模式服务不可达 | 配置 `MINERU_API_BASE_URL`，检查自建 MinerU 是否监听 |
| precise 模式鉴权失败 | 配置 `MINERU_API_TOKEN`，检查 `MINERU_PRECISE_BASE_URL` |
| 服务超时 | 检查 PDF 大小、`MINERU_REQUEST_TIMEOUT` / `MINERU_PRECISE_POLL_TIMEOUT` |

## 返回体

| 现象 | 处理 |
|------|------|
| 返回非 ZIP | 接口版本或模式不匹配；local 应返回 `application/zip` |
| ZIP 无 Markdown | 解析失败，勿伪造正文 |
| ZIP 无 `_middle.json`（precise 常见） | 正常；框架会 fallback 到 `layout.json` + `content_list` |

## 封面与正文

| 现象 | 处理 |
|------|------|
| `full.md` 缺少封面标准号 | 以 `layout.json` / `content_list` 补全；勿仅依赖 MD 前言 |
| `cover_metadata` 与文件名不一致 | 检查是否复用了旧 ZIP；删除 `zip/{stem}.zip` 后重跑 |
| `warnings` 含「图片几乎为空」 | MinerU 切图异常，需人工核对原 PDF |

## 图片

| 现象 | 处理 |
|------|------|
| 图片仍为 hash 文件名 | ZIP 中无 `content_list` 或无图题；会回退 `001.jpg` 序号命名 |
| MD 图片链接 404 | 确认使用工具返回的 `virtual_md_path` 相对路径，勿手工改目录结构 |
