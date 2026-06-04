# FastAPI BFF Phase 1 本地接口文档

> 适用范围：本地测试环境。\
> 目标：用 FastAPI + uvicorn 作为 BFF 代理，调用本地 LangGraph Server 托管的 Deep Agents 图，实现文件上传、标准审核、SSE 透传、HITL resume 和产物下载。

***

## 1. 本地架构

```text
前端 / 生成标准工作流 / curl / PowerShell
  |
  | HTTP + SSE
  v
FastAPI BFF
  http://127.0.0.1:8080
  |
  | langgraph-sdk
  v
LangGraph Server
  http://127.0.0.1:2024
  graphs:
    - agent
    - metadata_extraction
    - standard_review
```

Phase 1 中 FastAPI 不直接初始化智能体，只通过 `langgraph-sdk` 调用 LangGraph Server。

- 交互式审核入口调用 `agent` 图，适合前端/Studio 风格的人机协作。
- 结构化审核入口直接调用 `standard_review` 图，适合另一个生成智能体循环调用。

***

## 2. 启动服务

### 2.1 激活虚拟环境

```powershell
.\.venv\Scripts\Activate.ps1
```

### 2.2 安装依赖

```powershell
pip install -e ".[dev,documents,mineru,review]"
```

如果只测试 FastAPI 接口形状，不跑真实 PDF/DOCX 审核，可以先安装基础项目依赖：

```powershell
pip install -e .
```

### 2.3 设置本地环境变量

交互式审批测试：

```powershell
$env:LANGGRAPH_API_URL = "http://127.0.0.1:2024"
$env:STANDARD_DOC_ARTIFACT_API_BASE = "http://127.0.0.1:8080"
$env:STANDARD_DOC_ENABLE_WORKSPACE_BACKEND = "1"
# 如服务进程无法写入 D:\deep-agents\workspace，可指向可写目录
# $env:STANDARD_DOC_WORKSPACE_ROOT = "C:\Users\32084\AppData\Local\Temp\deep_agents_workspace"
```

生成标准工作流自动循环测试：

```powershell
$env:LANGGRAPH_API_URL = "http://127.0.0.1:2024"
$env:STANDARD_DOC_ARTIFACT_API_BASE = "http://127.0.0.1:8080"
$env:STANDARD_DOC_ENABLE_WORKSPACE_BACKEND = "1"
# 如服务进程无法写入 D:\deep-agents\workspace，可指向可写目录
# $env:STANDARD_DOC_WORKSPACE_ROOT = "C:\Users\32084\AppData\Local\Temp\deep_agents_workspace"
```

说明：

- `LANGGRAPH_API_URL`：FastAPI 调用的上游 LangGraph Server 地址。
- `STANDARD_DOC_ARTIFACT_API_BASE`：产物下载 URL 的 API 前缀。
- `STANDARD_DOC_ENABLE_WORKSPACE_BACKEND=1`：本地测试时允许 Deep Agents 内置文件工具访问 `/workspace/`。
- `STANDARD_DOC_WORKSPACE_ROOT`：可选，覆盖 `/workspace/` 的宿主机真实根目录。两个服务必须使用同一个值。

### 2.4 启动 LangGraph Server

第一个终端：

```powershell
langgraph dev --host 127.0.0.1 --port 2024 --no-browser
```

### 2.5 启动 FastAPI BFF

第二个终端：

```powershell
uvicorn standard_document_assistant.api.app:app --host 0.0.0.0 --port 8080 --reload
```

启动后可访问：

- Health: `http://127.0.0.1:8080/health`
- Swagger: `http://127.0.0.1:8080/docs`

***

## 3. 文件传递规则

审核接口只接受 Deep Agents 虚拟路径，例如 `/workspace/input/uploads/local-review-001/draft.md`。

两个项目处于不同工作目录时，不要把生成智能体本地磁盘路径直接传给审核服务。推荐流程是：

1. 生成智能体把草稿文件通过 `POST /api/threads/{thread_id}/uploads` 上传到 FastAPI。
2. FastAPI 返回 `virtual_path`。
3. 生成智能体把该 `virtual_path` 作为后续审核接口的 `file_path`。

如果两个项目在同一台机器并共享同一个 `/workspace/` 映射，也可以直接传共享虚拟路径；但本地联调默认推荐上传，避免路径根目录不一致。

***

## 4. 上传文件

创建 thread：

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8080/api/threads" `
  -ContentType "application/json" `
  -Body '{"thread_id":"draft-loop-001"}'
```

上传草稿：

```powershell
curl.exe -X POST `
  "http://127.0.0.1:8080/api/threads/draft-loop-001/uploads" `
  -F "file=@D:\path\to\draft.md"
```

返回示例：

```json
{
  "original_filename": "draft.md",
  "stored_filename": "draft.md",
  "virtual_path": "/workspace/input/uploads/draft-loop-001/draft.md",
  "suffix": ".md",
  "size_bytes": 12345,
  "sha256": "...",
  "content_type": "text/markdown"
}
```

后续审核使用 `virtual_path`。

***

## 5. 结构化非流式审核接口

### 5.1 适用情形

`POST /api/review-jobs/standard-review` 直接调用上游 `standard_review` 图，返回最终审核结果。它适合“生成标准文档”工作流做自动循环：

```text
生成草稿 -> 上传草稿 -> 结构化审核 -> 读取报告/结果 -> 修改草稿 -> 再审核 -> 通过后结束
```

该接口不依赖主 Agent 对自然语言的参数推断，因此适合精确控制“部分审核”和“单轨审核”。

### 5.2 请求体

```json
{
  "thread_id": "draft-loop-001",
  "file_path": "/workspace/input/uploads/draft-loop-001/draft.md",
  "source_path": null,
  "manifest_path": null,
  "output_subdir": null,
  "trace_id": null,
  "instruction": null,
  "review_options": {
    "mode": "content_only",
    "target_scopes": null,
    "line_start": null,
    "line_end": null,
    "partial_mode": null,
    "top_k": null,
    "force_rebuild_index": null,
    "disable_widen": false,
    "max_review_rounds": null
  },
  "return_report_content": true,
  "return_result_json": true
}
```

字段说明：

| 字段                      | 说明                                                 |
| ----------------------- | -------------------------------------------------- |
| `thread_id`             | 可选。用于产物登记和 LangGraph thread；不传则自动生成。               |
| `file_path`             | 必填。待审核文件虚拟路径。内容审核时通常是 Markdown；格式审核时可为 PDF/DOCX。   |
| `source_path`           | 可选。格式审核原始文件路径。`content_and_format` 时建议提供 PDF/DOCX。 |
| `manifest_path`         | 可选。已有 manifest 路径。                                 |
| `output_subdir`         | 可选。审核产物输出子目录。                                      |
| `review_options`        | 必填但有默认值。控制全文/部分/格式/内容审核。                           |
| `return_report_content` | 是否直接返回报告 Markdown 内容，默认 `true`。                    |
| `return_result_json`    | 是否直接返回结果 JSON，默认 `true`。                           |

### 5.3 review\_options

| 参数                             | 说明                                                                |
| ------------------------------ | ----------------------------------------------------------------- |
| `mode="content_only"`          | 默认。仅内容审核，按章节分轨，不做格式轨。                                             |
| `mode="content_and_format"`    | 内容审核 + 格式审核。建议 `file_path` 传 Markdown，`source_path` 传原始 PDF/DOCX。 |
| `mode="format_only"`           | 仅格式审核，不做内容规则判断。                                                   |
| `mode="scoped_content"`        | 部分章节内容审核。必须传 `target_scopes`。                                     |
| `mode="line_range_content"`    | Markdown 指定行范围内容审核。必须传 `line_start` 或 `line_end`。                 |
| `mode="full_document_content"` | 全文内容审核。                                                           |
| `target_scopes`                | 标准章节 canonical key，如 `["scope","normative_references"]`。          |
| `line_start` / `line_end`      | 1-based 行号。用于只审核 Markdown 某段。                                     |
| `disable_widen`                | 严格部分审核时设为 `true`，避免质量门控扩大到全文。                                     |
| `max_review_rounds`            | 覆盖审核回环次数。`0` 等价于禁止扩大审核。                                           |
| `top_k`                        | 覆盖规则检索数量。                                                         |
| `force_rebuild_index`          | 是否强制重建规则索引。                                                       |

常用章节 key：

| 中文说法    | `target_scopes`         |
| ------- | ----------------------- |
| 范围      | `scope`                 |
| 规范性引用文件 | `normative_references`  |
| 术语和定义   | `terms_definitions`     |
| 符号和缩略语  | `symbols_abbreviations` |
| 其他正文    | `other_body`            |
| 附录      | `appendix`              |
| 参考文献    | `references`            |

### 5.4 示例：仅审核“范围”和“规范性引用文件”

```powershell
curl.exe -X POST `
  "http://127.0.0.1:8080/api/review-jobs/standard-review" `
  -H "Content-Type: application/json" `
  -d "{\"thread_id\":\"draft-loop-001\",\"file_path\":\"/workspace/input/uploads/draft-loop-001/draft.md\",\"review_options\":{\"mode\":\"scoped_content\",\"target_scopes\":[\"scope\",\"normative_references\"],\"disable_widen\":true},\"return_report_content\":true,\"return_result_json\":true}"
```

### 5.5 本地脚本：上传并执行非流式审核

仓库提供联调脚本 [`scripts/test_fastapi_upload_review.py`](../scripts/test_fastapi_upload_review.py)，用于一次性测试：

1. `GET /health`
2. `POST /api/threads/{thread_id}/uploads`
3. `POST /api/review-jobs/standard-review`

若测试过程中注意到终端输出”卡住“，可以打开LangSmith Tracing，查看详细调用流程。大概率属于模型调用缓慢。

启动前提：激活虚拟环境，确保依赖已安装。

```powershell
# 终端 1：LangGraph Server
# $env:STANDARD_DOC_ENABLE_WORKSPACE_BACKEND = "1"
# $env:STANDARD_DOC_WORKSPACE_ROOT = "C:\Users\32084\AppData\Local\Temp\deep_agents_workspace"
# DashScope 外联不可用时，本地联调可临时开启
# $env:STANDARD_DOC_LLM_OFFLINE_FALLBACK = "1"
langgraph dev --host 127.0.0.1 --port 2024 --no-browser

# 终端 2：FastAPI BFF
# $env:LANGGRAPH_API_URL = "http://127.0.0.1:2024"
# $env:STANDARD_DOC_ARTIFACT_API_BASE = "http://127.0.0.1:8080"
# $env:STANDARD_DOC_ENABLE_WORKSPACE_BACKEND = "1"
# $env:STANDARD_DOC_WORKSPACE_ROOT = "C:\Users\32084\AppData\Local\Temp\deep_agents_workspace"
uvicorn standard_document_assistant.api.app:app --host 0.0.0.0 --port 8080
```

测试“仅审核前言”：

```powershell
python.exe scripts\test_fastapi_upload_review.py `
  --file "C:\Users\32084\Desktop\GB-T-15034-2009_2.md" `
  --mode scoped_content `
  --target-scopes foreword `
  --disable-widen `
  --force-rebuild-index `
  --save-response workspace\tmp\fastapi_review_response.json
```

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--file` | 本地待上传文件。脚本会先调用上传接口，后续审核使用上传返回的 `virtual_path`。 |
| `--mode` | 对应 `review_options.mode`，如 `scoped_content`、`content_only`、`format_only`。 |
| `--target-scopes` | 逗号分隔章节 key，如 `foreword` 或 `scope,normative_references`。 |
| `--disable-widen` | 映射为 `review_options.disable_widen=true`，严格部分审核时使用。 |
| `--force-rebuild-index` | 映射为 `review_options.force_rebuild_index=true`，规则更新后建议使用。 |
| `--save-response` | 保存完整审核响应 JSON，便于分析 `issues`、报告和产物路径。 |

输出解读：

- `uploaded_virtual_path`：上传接口返回的 `/workspace/...` 路径。
- `status=completed`：FastAPI 已拿到标准审核最终结果；不代表审核通过。
- `passed=false`：结构化审核未通过，生成工作流应读取报告并修改草稿。
- `summary.total_issues / failed / warn / insufficient_context`：问题总数、失败数、警告数、依据不足数。
- `issues_by_scope`：用于确认部分审核是否集中在目标章节。
- `output_paths`：标准审核图生成的报告、结果、trace、manifest 虚拟路径。
- `registered_artifacts`：FastAPI 登记后的可下载产物。
- `report_preview`：报告 Markdown 的前 500 字，完整内容在保存的 JSON 中。

如果出现 `HTTP 502` 且 detail 包含 `/runs/wait` 的 `404 Not Found`，说明 FastAPI 进程仍是旧代码；重启 uvicorn 后再测。当前实现会在 `runs.wait` 不可用时自动改用 stream 收集最终 state。

### 5.6 示例：全文内容审核

```json
{
  "thread_id": "draft-loop-001",
  "file_path": "/workspace/input/uploads/draft-loop-001/draft.md",
  "review_options": {
    "mode": "full_document_content"
  }
}
```

### 5.7 示例：内容 + 格式审核

```json
{
  "thread_id": "draft-loop-001",
  "file_path": "/workspace/input/uploads/draft-loop-001/draft.md",
  "source_path": "/workspace/input/uploads/draft-loop-001/draft.docx",
  "review_options": {
    "mode": "content_and_format"
  }
}
```

### 5.8 示例：仅格式审核

```json
{
  "thread_id": "draft-loop-001",
  "file_path": "/workspace/input/uploads/draft-loop-001/draft.docx",
  "review_options": {
    "mode": "format_only"
  }
}
```

### 5.9 示例：按行范围审核

```json
{
  "thread_id": "draft-loop-001",
  "file_path": "/workspace/input/uploads/draft-loop-001/draft.md",
  "review_options": {
    "mode": "line_range_content",
    "line_start": 20,
    "line_end": 80,
    "disable_widen": true
  }
}
```

### 5.10 返回体

```json
{
  "status": "completed",
  "thread_id": "draft-loop-001",
  "passed": false,
  "review": {
    "status": "success",
    "summary": {
      "total": 8,
      "passed": 6,
      "failed": 2,
      "warnings": 0,
      "errors": 0
    },
    "report_path": "/workspace/output/reviews/.../audit_report.md",
    "result_path": "/workspace/output/reviews/.../review_result.json"
  },
  "review_report_markdown": "## 标准文档审核报告\n...",
  "review_result": {
    "issues": []
  },
  "artifacts": {
    "review_report": {
      "artifact_id": "...",
      "download_url": "http://127.0.0.1:8080/api/threads/draft-loop-001/artifacts/.../download"
    },
    "review_result": {
      "artifact_id": "...",
      "download_url": "http://127.0.0.1:8080/api/threads/draft-loop-001/artifacts/.../download"
    }
  },
  "review_options": {
    "mode": "scoped_content",
    "target_scopes": ["scope", "normative_references"],
    "disable_widen": true
  }
}
```

生成智能体建议优先读取：

- `passed`：程序化通过/不通过判断。
- `review_report_markdown`：给模型作为修改依据。
- `review_result`：结构化定位问题、严重级别和规则依据。
- `artifacts.*.download_url`：需要归档或人工查看时下载报告文件。

***

## 6. 结构化流式审核接口

`POST /api/review-jobs/standard-review/stream` 与非流式接口使用同一个请求体，也支持完整 `review_options`。

适用情形：

- 前端需要展示审核进度。
- 生成工作流希望边审边记录 trace，但仍在 `review.completed` 事件中读取最终结果。

示例：

```powershell
curl.exe -N -X POST `
  "http://127.0.0.1:8080/api/review-jobs/standard-review/stream" `
  -H "Content-Type: application/json" `
  -d "{\"thread_id\":\"draft-loop-001\",\"file_path\":\"/workspace/input/uploads/draft-loop-001/draft.md\",\"review_options\":{\"mode\":\"scoped_content\",\"target_scopes\":[\"scope\",\"normative_references\"],\"disable_widen\":true}}"
```

常见 SSE 事件：

```text
event: run.started
data: {"thread_id":"draft-loop-001","assistant_id":"standard_review"}

event: agent.progress
data: {"type":"review.retrieve.rules","active_scopes":["scope","normative_references"]}

event: review.snapshot
data: {"job_id":"...","trace_id":"..."}

event: review.completed
data: {"status":"completed","passed":false,"review_report_markdown":"...","review_result":{...}}

event: run.completed
data: {"thread_id":"draft-loop-001"}
```

***

## 7. 交互式主 Agent 审核接口

`POST /api/threads/{thread_id}/standard-review/stream` 调用上游 `agent` 图。它会把 `file_path` 和 `instruction` 组织成自然语言消息交给主 Agent，适合交互式审核。

```powershell
curl.exe -N -X POST `
  "http://127.0.0.1:8080/api/threads/local-review-001/standard-review/stream" `
  -H "Content-Type: application/json" `
  -d "{\"file_path\":\"/workspace/input/uploads/local-review-001/standard.pdf\"}"
```

该入口可以处理用户自然语言，例如：

```json
{
  "file_path": "/workspace/input/uploads/t/draft.md",
  "instruction": "请仅审核范围和规范性引用文件部分。"
}
```

但它依赖主 Agent / reviewer 对自然语言的理解，不建议作为另一个智能体的稳定机器接口。若需要严格触发 `target_scopes=["scope","normative_references"]`，应使用结构化审核接口。

***

## 8. HITL resume

如果交互式 SSE 中出现 `approval.required`，说明上游图暂停等待人工决策。批准继续：

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8080/api/threads/local-review-001/runs/resume" `
  -ContentType "application/json" `
  -Body '{"action":"approve"}'
```

带修改意见继续：

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8080/api/threads/local-review-001/runs/resume" `
  -ContentType "application/json" `
  -Body '{"action":"edit","message":"允许解析，但输出目录请使用默认 workspace/output。"}'
```

拒绝：

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8080/api/threads/local-review-001/runs/resume" `
  -ContentType "application/json" `
  -Body '{"action":"reject","message":"本次不允许调用该工具。"}'
```

***

## 9. 产物查询与下载

查看产物列表：

```powershell
Invoke-RestMethod `
  -Method Get `
  -Uri "http://127.0.0.1:8080/api/threads/draft-loop-001/artifacts"
```

下载产物：

```powershell
curl.exe -L `
  "http://127.0.0.1:8080/api/threads/draft-loop-001/artifacts/{artifact_id}/download" `
  -o ".\audit_report.md"
```

结构化审核接口默认已经直接返回报告内容和结果 JSON；下载接口主要用于归档、前端预览或人工复核。

***

## 10. 生成标准工作流循环调用建议

推荐另一个生成智能体按以下方式接入：

1. 生成或修改草稿 Markdown。
2. 上传草稿，保存返回的 `virtual_path`。
3. 调用 `POST /api/review-jobs/standard-review`。
4. 模型读取 `review_report_markdown` 和 `review_result`，判断是否需要修改。
5. 如果 `passed=false` 或模型判断需修订，生成新版本草稿并重复 2-4。
6. 如果 `passed=true` 且模型确认审核意见已闭环，结束生成流程。

推荐请求：

```json
{
  "thread_id": "draft-loop-001",
  "file_path": "/workspace/input/uploads/draft-loop-001/draft-v3.md",
  "review_options": {
    "mode": "content_only",
    "disable_widen": false
  },
  "return_report_content": true,
  "return_result_json": true
}
```

如果生成工作流只改了“范围”和“规范性引用文件”，为了降低成本并避免无关章节干扰，可使用：

```json
{
  "thread_id": "draft-loop-001",
  "file_path": "/workspace/input/uploads/draft-loop-001/draft-v3.md",
  "review_options": {
    "mode": "scoped_content",
    "target_scopes": ["scope", "normative_references"],
    "disable_widen": true
  },
  "return_report_content": true,
  "return_result_json": true
}
```

如果需要最终定稿前的兜底检查，建议最后再跑一次：

```json
{
  "thread_id": "draft-loop-001",
  "file_path": "/workspace/input/uploads/draft-loop-001/draft-final.md",
  "source_path": "/workspace/input/uploads/draft-loop-001/draft-final.docx",
  "review_options": {
    "mode": "content_and_format"
  }
}
```

***

## 11. 接口清单

| 方法     | 路径                                                          | 说明                            |
| ------ | ----------------------------------------------------------- | ----------------------------- |
| `GET`  | `/health`                                                   | FastAPI BFF 健康检查              |
| `POST` | `/api/threads`                                              | 创建 LangGraph thread           |
| `POST` | `/api/threads/{thread_id}/uploads`                          | 上传文件并返回 `/workspace/` 虚拟路径    |
| `POST` | `/api/review-jobs/standard-review`                          | 结构化非流式标准审核，适合生成工作流            |
| `POST` | `/api/review-jobs/standard-review/stream`                   | 结构化流式标准审核，支持 `review_options` |
| `POST` | `/api/threads/{thread_id}/runs/stream`                      | 通用主 Agent 流式运行                |
| `POST` | `/api/threads/{thread_id}/standard-review/stream`           | 交互式标准审核入口，走主 Agent 自然语言理解     |
| `POST` | `/api/threads/{thread_id}/runs/resume`                      | HITL 审批恢复                     |
| `GET`  | `/api/threads/{thread_id}/artifacts`                        | 查看 thread 产物列表                |
| `GET`  | `/api/threads/{thread_id}/artifacts/{artifact_id}/download` | 下载产物                          |

***

## 12. 本地排错

### FastAPI 返回 `run.failed`

优先检查：

1. `langgraph dev` 是否仍在 `http://127.0.0.1:2024` 运行。
2. `LANGGRAPH_API_URL` 是否指向正确端口。
3. `.env` 是否配置模型 API key。
4. PDF/DOCX 审核时 MinerU 本地服务或云端配置是否可用。
5. 文件路径是否是 `/workspace/...` 虚拟路径。
6. 自动循环场景是否保持 HITL 关闭。

### LLM Judge 返回 `Connection error`

如果报告中出现 `LLM 审核异常：Connection error`，通常表示服务进程无法连接 DashScope，而不是审核规则未执行。请先确认本机能访问 `https://dashscope.aliyuncs.com/compatible-mode/v1`，并按本机网络环境设置 `HTTP_PROXY` / `HTTPS_PROXY`。

本地离线联调可临时设置：

```powershell
$env:STANDARD_DOC_LLM_OFFLINE_FALLBACK = "1"
```

开启后，LLM Judge 连接失败会返回可解析的保守审核结果，便于验证上传、规则检索、部分审核和产物生成链路。真实审核应关闭该开关。

### 上传成功但审核读不到文件

本地测试建议设置：

```powershell
$env:STANDARD_DOC_ENABLE_WORKSPACE_BACKEND = "1"
$env:STANDARD_DOC_WORKSPACE_ROOT = "C:\Users\32084\AppData\Local\Temp\deep_agents_workspace"
```

并确认传给审核接口的是上传返回的 `virtual_path`，不是 Windows 盘符路径。

### 部分审核被扩大到全文

标准审核子图的质量门控在依据不足时可能扩大审核范围。严格部分审核时，在 `review_options` 中设置：

```json
{
  "disable_widen": true
}
```

或：

```json
{
  "max_review_rounds": 0
}
```

### 产物没有 `download_url`

确认 FastAPI 启动前设置：

```powershell
$env:STANDARD_DOC_ARTIFACT_API_BASE = "http://127.0.0.1:8080"
```

***

## 13. Phase 1 边界

本阶段只覆盖本地联调：

- 不实现生产 JWT 鉴权。
- 不实现多租户目录隔离。
- 不实现 Nginx/Caddy TLS。
- 不实现 SSE 断点续传。
- 不替换 LangGraph Server 的持久化后端。

这些内容可在 Phase 2/Phase 3 中继续推进。
