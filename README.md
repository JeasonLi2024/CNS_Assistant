# 标准文档助手

这是一个基于 Deep Agents SDK 的标准文档助手实现，当前主链路聚焦用户上传标准文档后的 PDF 解析和国标元数据抽取：上传文件保存到 `/workspace/input/uploads/`，PDF 通过 MinerU 解析为 Markdown，Markdown 通过 LangGraph 子图抽取结构化元数据，并将结果和 manifest 写入 `/workspace/output/`。

## 结构

项目保留了 Deep Agents 官方文档中可迁移的 agent 文件形状：

- `AGENTS.md`：主 Agent 指令。
- `skills/`：按需加载的标准解析、审核、起草、抽取技能。
- `subagents/`：parser、reviewer、writer、extractor、research 的说明文件。
- `tools.json`：托管 Deep Agents 形态下的工具说明和审批配置。

SDK 实现在 `src/standard_document_assistant/`：

- `agent.py`：`create_deep_agent()` 工厂、`CompositeBackend`、permissions、memory seed、subagents。
- `tools/`：正式业务工具，包含 `parse_pdf_with_mineru`、`extract_standard_metadata`、schema 校验和记忆提案。
- `graphs/metadata_extraction/`：元数据抽取 LangGraph 子图。
- `integrations/mineru/`：MinerU HTTP 和 ZIP 后处理集成。
- `uploads.py`：应用层上传文件保存辅助函数。
- `schemas.py`：Pydantic 结构化输出。
- `streaming.py`：Deep Agents stream update 到 SSE 事件的映射骨架。
- `config.py`：`.env` 和 `config.yaml` 配置读取。

## 安装

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python.exe -m pip install -U pip
python.exe -m pip install -e .[documents,mineru,dev]
```

复制 `.env.example` 为 `.env`，填入 `DASHSCOPE_API_KEY`、`MINERU_API_BASE_URL` 和可选的 LangSmith 配置。

## 验证最小工具链

```powershell
python scripts\smoke_test.py
```

该脚本会创建一个 Markdown 上传样本，验证上传保存、元数据抽取、manifest 写入、schema 校验和项目结构。生成文件位于 `workspace/output/metadata/`。

## 检查 Deep Agent 配置

```powershell
standard-doc-assistant --print-config-only
```

如果未安装依赖，也可以先只运行 `python -m compileall src scripts tests` 做语法检查。

## 运行示例

```powershell
standard-doc-assistant "请抽取 /workspace/input/uploads/standard-doc-session-001/sample_standard.md 的元数据" --thread-id standard-doc-session-001 --strict-model
```

`--strict-model` 会要求已安装 `langchain-qwq` 且存在 `DASHSCOPE_API_KEY`。CLI 本地模式使用 `MemorySaver` 和 `InMemoryStore`，它们不是生产持久化方案。

## LangGraph 本地调试（LangSmith Deployment）

项目根目录已提供 `langgraph.json` 和 `agent.py`，可直接用 LangGraph CLI 启动本地 Agent Server（默认 `http://127.0.0.1:2024`）并在 LangGraph Studio 中调试。

```powershell
# 安装开发依赖（含 langgraph-cli[inmem]）
python.exe -m pip install -e .[documents,mineru,dev]

# 启动本地开发服务器（热重载）
langgraph dev
```

常用参数：

```powershell
langgraph dev --port 2024 --no-browser
langgraph dev --config .\langgraph.json
```

说明：

- Graph ID：`agent`（主编排，`agent.py:agent`）、`metadata_extraction`（元数据子图，`metadata_extraction_graph.py:metadata_extraction`）。
- **LangSmith 追踪**：主编排通过 `task` → `extractor` → `extract_standard_metadata` 调用子图时，工具会把父级 `RunnableConfig`（callbacks/tags/metadata）传入子图，在 LangSmith 中展开为嵌套 run（`metadata_extraction` → 各节点，含 `run_langextract`）。在 Studio 中选 `metadata_extraction` graph 可单独调试子图拓扑；在 thread trace 中展开 `extract_standard_metadata` 工具 run 可查看与主编排的协作关系。需开启 `LANGSMITH_TRACING=true` 且配置 `LANGSMITH_API_KEY`。
- `langgraph dev` 会读取 `.env` 中的 `DASHSCOPE_API_KEY` 和 LangSmith 配置。
- LangGraph Server 模式不会注入 CLI 用的 `MemorySaver` / `InMemoryStore`，持久化由 dev server 或部署平台提供。
- Deep Agents 内置文件工具只能使用虚拟路径，例如 `/workspace/input/sample.md`、`/memories/preferences.md`、`/skills/standard-review/SKILL.md`；不要在 Studio 中使用 `D:\deep-agents` 这类 Windows 绝对路径。
- `langgraph_server=True` 时默认不映射 `/workspace/` 到宿主机文件系统；本地调试如需处理 `workspace/` 真实文件，可显式设置 `STANDARD_DOC_ENABLE_WORKSPACE_BACKEND=1`。
- 本地调试默认通过 `/skills/` 虚拟路径读取项目内 `skills/` 目录；生产部署如不希望依赖宿主机文件系统，可设置 `STANDARD_DOC_ENABLE_LOCAL_SKILLS_BACKEND=0`，并将 `/skills/` 内容预置到平台 Store/Context Hub。
- 长期记忆采用“提案式写入”：Agent 调用 `propose_memory_update` 生成待审批提案，不直接写 `/memories/`；审批通过后应由应用层在用户隔离的 Store namespace 中校验、合并并持久化。
- **HITL（人工审批）**：`langgraph_server=True`（`langgraph dev`）时默认**关闭**工具审批，避免 Studio 在 Resume 框填 `""` 或 `"approve"` 触发 `TypeError: string indices must be integers, not 'str'`。需要审批时在 `.env` 设置 `STANDARD_DOC_ENABLE_HITL=1`，Resume 必须使用 JSON 对象（RAW 模式）：

```json
{"decisions": [{"type": "approve"}]}
```

  拒绝示例：`{"decisions": [{"type": "reject", "message": "暂不执行"}]}`。自建 API 可用 `streaming.build_resume_command("approve")` 生成等价的 `Command(resume=...)`。

部署到 LangSmith：

```powershell
langgraph deploy --name standard-document-assistant
```

需要 Docker 和 `LANGSMITH_API_KEY`。

## 当前边界

- PDF 解析依赖外部 MinerU 服务，需要配置 `MINERU_API_BASE_URL`。
- Word/DOCX 转换、正式参考检索、正式审核报告工具、正式起草工具尚未接入。
- `vision_parser` 暂不启用，后续需要单独设计视觉/OCR 边界。
- SSE 和审批接口已提供代码骨架，尚未绑定具体 Web 框架。
