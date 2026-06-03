# FastAPI BFF Phase 1 本地接口文档

> 适用范围：本地测试环境。  
> 目标：用 FastAPI + uvicorn 作为 BFF 代理，调用本地 LangGraph Server 托管的 Deep Agents 图，实现标准文档上传、审核流式运行、HITL 恢复和产物下载。

---

## 1. 本地架构

```text
前端 / curl / PowerShell
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

Phase 1 中 FastAPI 不直接初始化主智能体，只通过 `langgraph-sdk` 调用 LangGraph Server。

---

## 2. 启动服务

### 2.1 安装依赖

在项目根目录执行：

```powershell
pip install -e ".[dev,documents,mineru,review]"
```

如果只测试 FastAPI 接口形状，不跑真实 PDF/DOCX 审核，可以先安装基础项目依赖：

```powershell
pip install -e .
```

### 2.2 设置本地环境变量

```powershell
$env:LANGGRAPH_API_URL = "http://127.0.0.1:2024"
$env:STANDARD_DOC_ARTIFACT_API_BASE = "http://127.0.0.1:8080"
$env:STANDARD_DOC_ENABLE_HITL = "1"
$env:STANDARD_DOC_ENABLE_WORKSPACE_BACKEND = "1"
```

说明：

- `LANGGRAPH_API_URL`：FastAPI 调用的上游 LangGraph Server 地址。
- `STANDARD_DOC_ARTIFACT_API_BASE`：产物下载 URL 的 API 前缀。
- `STANDARD_DOC_ENABLE_HITL=1`：本地验证审批流时启用 HITL。
- `STANDARD_DOC_ENABLE_WORKSPACE_BACKEND=1`：本地测试时允许 Deep Agents 内置文件工具访问 `/workspace/`。

### 2.3 启动 LangGraph Server

打开第一个终端，在项目根目录执行：

```powershell
langgraph dev --host 127.0.0.1 --port 2024 --no-browser
```

启动后可访问：

- API: `http://127.0.0.1:2024`
- Docs: `http://127.0.0.1:2024/docs`
- Studio: 根据终端输出打开 Studio URL

### 2.4 启动 FastAPI BFF

打开第二个终端，在项目根目录执行：

```powershell
uvicorn standard_document_assistant.api.app:app --host 0.0.0.0 --port 8080 --reload
```

启动后可访问：

- Health: `http://127.0.0.1:8080/health`
- Swagger: `http://127.0.0.1:8080/docs`

---

## 3. 标准文档审核操作流程

### 步骤 1：创建 thread

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8080/api/threads" `
  -ContentType "application/json" `
  -Body '{"thread_id":"local-review-001"}'
```

返回示例：

```json
{
  "thread_id": "local-review-001",
  "created_at": "..."
}
```

### 步骤 2：上传标准文档

PDF、DOCX、Markdown 均可通过 FastAPI 保存到 `/workspace/input/uploads/{thread_id}/`。

```powershell
curl.exe -X POST `
  "http://127.0.0.1:8080/api/threads/local-review-001/uploads" `
  -F "file=@D:\path\to\standard.pdf"
```

返回示例：

```json
{
  "original_filename": "standard.pdf",
  "stored_filename": "standard.pdf",
  "virtual_path": "/workspace/input/uploads/local-review-001/standard.pdf",
  "suffix": ".pdf",
  "size_bytes": 123456,
  "sha256": "...",
  "content_type": "application/pdf"
}
```

后续审核使用 `virtual_path`，不要使用 Windows 盘符路径。

### 步骤 3：发起标准文档审核

使用专用审核入口：

```powershell
curl.exe -N -X POST `
  "http://127.0.0.1:8080/api/threads/local-review-001/standard-review/stream" `
  -H "Content-Type: application/json" `
  -d "{\"file_path\":\"/workspace/input/uploads/local-review-001/standard.pdf\"}"
```

该接口返回 `text/event-stream`。常见事件：

```text
event: run.started
data: {"run_id":"run_xxx","thread_id":"local-review-001","assistant_id":"agent"}

event: agent.progress
data: {"type":"mineru.parse.completed", "...":"..."}

event: plan.updated
data: {"todos":[...]}

event: approval.required
data: {"interrupt":[...]}

event: artifact.created
data: {"artifact_id":"...","download_url":"http://127.0.0.1:8080/api/threads/.../download"}

event: run.completed
data: {"artifact_ids":["..."]}
```

如果上传的是 Markdown，也可直接发起审核：

```powershell
curl.exe -N -X POST `
  "http://127.0.0.1:8080/api/threads/local-review-001/standard-review/stream" `
  -H "Content-Type: application/json" `
  -d "{\"file_path\":\"/workspace/input/uploads/local-review-001/standard.md\",\"instruction\":\"请重点检查元数据和格式来源问题。\"}"
```

### 步骤 4：处理 HITL 审批

如果 SSE 中出现 `approval.required`，说明上游图暂停等待人工决策。批准继续：

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

### 步骤 5：查看产物列表

```powershell
Invoke-RestMethod `
  -Method Get `
  -Uri "http://127.0.0.1:8080/api/threads/local-review-001/artifacts"
```

返回示例：

```json
{
  "thread_id": "local-review-001",
  "artifacts": [
    {
      "artifact_id": "abc123",
      "tool": "run_standard_review",
      "artifact_type": "review_report",
      "virtual_path": "/workspace/output/reviews/.../audit_report.md",
      "download_url": "http://127.0.0.1:8080/api/threads/local-review-001/artifacts/abc123/download"
    }
  ]
}
```

### 步骤 6：下载产物

```powershell
curl.exe -L `
  "http://127.0.0.1:8080/api/threads/local-review-001/artifacts/abc123/download" `
  -o ".\audit_report.md"
```

---

## 4. 通用运行接口

如果不使用专用标准审核入口，也可以直接调用主 Agent。

```powershell
curl.exe -N -X POST `
  "http://127.0.0.1:8080/api/threads/local-review-001/runs/stream" `
  -H "Content-Type: application/json" `
  -d "{\"message\":\"请审核 /workspace/input/uploads/local-review-001/standard.pdf，并输出报告。\"}"
```

也可以传原始 LangGraph input：

```json
{
  "input": {
    "messages": [
      {
        "role": "user",
        "content": "请审核 /workspace/input/uploads/local-review-001/standard.pdf"
      }
    ]
  },
  "stream_modes": ["custom", "updates"],
  "stream_subgraphs": true
}
```

---

## 5. 接口清单

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/health` | FastAPI BFF 健康检查 |
| `POST` | `/api/threads` | 创建 LangGraph thread |
| `POST` | `/api/threads/{thread_id}/uploads` | 上传文件并返回 `/workspace/` 虚拟路径 |
| `POST` | `/api/threads/{thread_id}/runs/stream` | 通用主 Agent 流式运行 |
| `POST` | `/api/threads/{thread_id}/standard-review/stream` | 标准文档审核专用流式入口 |
| `POST` | `/api/threads/{thread_id}/runs/resume` | HITL 审批恢复 |
| `GET` | `/api/threads/{thread_id}/artifacts` | 查看 thread 产物列表 |
| `GET` | `/api/threads/{thread_id}/artifacts/{artifact_id}/download` | 下载产物 |

---

## 6. 本地排错

### FastAPI 返回 `run.failed`

优先检查：

1. `langgraph dev` 是否仍在 `http://127.0.0.1:2024` 运行。
2. `LANGGRAPH_API_URL` 是否指向正确端口。
3. `.env` 是否配置模型 API key。
4. PDF/DOCX 审核时 MinerU 本地服务或云端配置是否可用。
5. 文件路径是否是 `/workspace/...` 虚拟路径。

### 上传成功但审核读不到文件

本地测试建议设置：

```powershell
$env:STANDARD_DOC_ENABLE_WORKSPACE_BACKEND = "1"
```

并确认传给审核接口的是上传返回的 `virtual_path`，不是 `host_path`。

### 产物没有 `download_url`

确认 FastAPI 启动前设置：

```powershell
$env:STANDARD_DOC_ARTIFACT_API_BASE = "http://127.0.0.1:8080"
```

---

## 7. Phase 1 边界

本阶段只覆盖本地联调：

- 不实现生产 JWT 鉴权。
- 不实现多租户目录隔离。
- 不实现 Nginx/Caddy TLS。
- 不实现 SSE 断点续传。
- 不替换 LangGraph Server 的持久化后端。

这些内容可在 Phase 2/Phase 3 中继续推进。
