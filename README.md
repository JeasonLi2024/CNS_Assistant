# 国家标准文档助手（CNA Agent based on Deep Agents）

> 基于 **Deep Agents** + **LangGraph** 的国家级 / 行业级标准文档智能助手：解析 → 元数据抽取 → 标准审核 → 报告生成 → 长期记忆。
> 框架严格遵循 Deep Agents 规范（虚拟文件路径、HITL、子代理、Skills、Memory 提案、LangSmith Trace），并落地了 **"FAISS + LLM Judge 多策略 + 质量门控 + 范围扩大回环 + scope\_summary + audit\_summary"** 完整内容审核链路。
> 当前 `langgraph.json` 同时注册了 3 个图：`agent`（主 Agent）、`metadata_extraction`（元数据抽取子图）、`standard_review`（标准审核子图），可在 LangGraph Studio 同时可视化。
>
> 开发建议：使用LangChain MCP和官方Skills——[GitHub - langchain-ai/langchain-skills · GitHub](https://github.com/langchain-ai/langchain-skills)；通过LangSmith可视化观测追踪。模式设计可以参考字节[Deer Flow](https://github.com/bytedance/deer-flow)。

***

## 1. 整体开发成果

### 1.1 工程定位

- **目标场景**：标准文档的检索、生成、审核、格式转化、关键信息抽取。
- **技术栈**：
  - **Deep Agents**：`create_deep_agent` 风格的子代理 + middleware 编排（Skills / SubAgent / Filesystem / HITL / Memory / Summarization）。
  - **LangGraph**：`StateGraph` 状态机子图 + `Command[Literal[...]]` 条件边 + 多轮回环 + `Send` 并行扇出。
  - **LangChain**：`ChatOpenAI` 兼容 DashScope Qwen / `FakeListChatModel` 离线回退。
  - **LangSmith**：可观测性 + trace + subagent 流式事件。
  - **MinerU**：`local` 自建 / `precise` 在线双模 PDF/DOCX 解析。
  - **scikit-learn + faiss-cpu**：审核规则 TF-IDF + IndexFlatIP 索引（缺包时退到纯 Python TF-IDF JSON）。

### 1.2 核心特性

| 维度                    | 能力                                                                                               |
| --------------------- | ------------------------------------------------------------------------------------------------ |
| **多 Agent 编排**        | 1 个主 Agent + 5 个 subagent（parser / extractor / reviewer / research / writer）                     |
| **Skills on-demand**  | 4 个技能包：`standard-parsing` / `standard-extraction` / `standard-review` / `standard-drafting`，按需加载 |
| **HITL**              | 9 个高敏工具走 `interrupt_on`（写入、解析、抽取、审核、索引、记忆、执行等）                                                   |
| **虚拟文件**              | 全部 IO 限定在 `/workspace/...`，`FilesystemPermission` 强制落点                                           |
| **Memory（未做持久化）**     | 短期（State）+ 工作区（Filesystem）+ 长期（Store），记忆更新走提案制                                                   |
| **Trace**             | `invoke_traced_graph` 注入父级 callbacks；子图以独立 graph id 呈现                                           |
| **子图**                | `metadata_extraction`、`standard_review` 两个 LangGraph 子图（均带 `Command` 条件边），全部注册到 `langgraph.json` |
| **Send 并行（尚未实现）**     | `judge_rules` 内 `asyncio.gather` + `Semaphore` 对 scope/rule 二维分组扇出                               |
| **Stream（仅实现统一结构）**   | `state["trace_events"]` + `get_stream_writer` 双通道，命名空间统一 `<domain>.<stage>`                      |
| **FastAPI BFF**       | 本地 `uvicorn` 代理后端：上传、SSE 透传、HITL resume、产物下载                                                     |
| **多用户 runtime（尚未实现）** | `_memory_namespace_factory` 按 LangGraph Server `server_info.user.identity` 隔离                    |
| **离线可跑**              | FAISS 不可用时回退 TF-IDF JSON；LLM 无 key 时回退 `FakeListChatModel`                                       |
| **Studio 可视化**        | `langgraph dev` 即可在 <http://localhost:2024> 看到主图 + 两个子图                                          |

### 1.3 `langgraph.json` 注册的图

```json
{
  "dependencies": ["."],
  "graphs": {
    "agent": "./agent.py:agent",
    "metadata_extraction": "./metadata_extraction_graph.py:metadata_extraction",
    "standard_review": "./standard_review_graph.py:standard_review"
  },
  "env": ".env",
  "python_version": "3.12"
}
```

| Graph ID              | 入口文件                                                                                  | 符号                    | 说明                                                   |
| --------------------- | ------------------------------------------------------------------------------------- | --------------------- | ---------------------------------------------------- |
| `agent`               | [agent.py](file:///d:/deep-agents/agent.py)                                           | `agent`               | Deep Agents 主图：包含 5 个 subagent + Skills + middleware |
| `metadata_extraction` | [metadata\_extraction\_graph.py](file:///d:/deep-agents/metadata_extraction_graph.py) | `metadata_extraction` | Langextract 元数据抽取子图                                  |
| `standard_review`     | [standard\_review\_graph.py](file:///d:/deep-agents/standard_review_graph.py)         | `standard_review`     | 标准审核子图（FAISS + LLM Judge + 格式轨 + 回环）                 |

### 1.4 仓库目录

```text
d:\deep-agents\
├── AGENTS.md                       # 主 Agent 工作约束
├── agent.py                        # LangGraph Server 入口：Deep Agents 编译入口
├── metadata_extraction_graph.py    # langgraph.json → metadata_extraction 入口
├── standard_review_graph.py        # langgraph.json → standard_review 入口
├── config.yaml                     # 应用/模型/MinerU/审核/记忆 配置
├── langgraph.json                  # LangGraph Server 部署配置（3 个 graph）
├── pyproject.toml                  # Python 项目元数据 + 依赖
├── .env.example                    # 环境变量模板（git-ignored 的 .env 由此复制）
│
├── src/standard_document_assistant/
│   ├── config.py                  # 强类型配置 dataclass + 加载 + Qwen 模型构建
│   ├── constants.py               # 路径 / 命名空间常量
│   ├── pathing.py                 # 虚拟路径 ↔ 真实路径 + Trace / 产物 IO
│   ├── tracing.py                 # invoke_traced_graph / 节点名 / 工具名常量
│   ├── schemas.py                 # Pydantic 数据契约
│   ├── agent.py                   # 主 Agent + middleware + permissions + subagents
│   ├── artifacts.py               # 产物下载链接、注册器
│   ├── streaming.py               # SSE 流式响应包装
│   ├── api/                       # FastAPI BFF：上传 / 流式运行 / HITL / 下载
│   │   ├── app.py                 # uvicorn 入口：standard_document_assistant.api.app:app
│   │   ├── models.py
│   │   ├── settings.py
│   │   ├── langgraph_client.py
│   │   └── sse_adapter.py
│   ├── tools/
│   │   ├── parser.py              # parse_file_with_mineru（sync + async 双实现）
│   │   ├── metadata.py            # extract_standard_metadata
│   │   ├── review.py              # 6 个审核工具（含 build_review_index / inspect_review_rules）
│   │   └── validation.py          # validate_output_schema / propose_memory_update
│   ├── graphs/
│   │   ├── metadata_extraction/   # langextract 元数据抽取子图
│   │   │   ├── graph.py           # StateGraph：parse → chunk → extract → validate → artifact
│   │   │   ├── state.py
│   │   │   ├── nodes.py
│   │   │   ├── prompts.py
│   │   │   └── langextract_runner.py
│   │   └── standard_review/       # 标准审核子图
│   │       ├── graph.py           # StateGraph：ingest→retrieve→judge→quality_gate→...
│   │       ├── state.py
│   │       ├── events.py          # 统一 review.* 事件发射
│   │       └── nodes/             # 6 个拆分节点
│   │           ├── ingest.py
│   │           ├── retrieve.py
│   │           ├── review.py      # judge_rules / quality_gate / widen / reload
│   │           ├── format_review.py
│   │           ├── aggregate.py
│   │           └── report.py      # write_outputs / write_manifest
│   ├── review_core/               # 审核核心
│   │   ├── doc_parser.py
│   │   ├── word_parser.py
│   │   ├── pdf_format_parser.py
│   │   ├── format_audit.py
│   │   ├── knowledge_base.py      # RuleKnowledgeBase（含 build_faiss / from_faiss_index）
│   │   ├── retriever.py           # 兼容旧接口；新检索器在 retrievers/
│   │   ├── retrievers/            # faiss-cpu + sklearn 检索器
│   │   │   ├── __init__.py
│   │   │   └── vector_retriever.py
│   │   ├── context_chunker.py     # DocumentContextBuilder
│   │   ├── llm_client.py          # ChatOpenAI 兼容 DashScope + 离线回退
│   │   ├── llm_judge.py           # LLMSoftRuleJudge 4 策略
│   │   ├── audit_summary.py       # LLM 报告摘要 + 离线 fallback
│   │   ├── rule_models.py
│   │   ├── rules.py
│   │   ├── reporter.py
│   │   ├── serialization.py
│   │   └── scopes.py
│   ├── integrations/mineru/       # local / precise 双模 MinerU 客户端
│   │   ├── client.py              # HTTP 客户端 + 异步轮询
│   │   ├── config.py
│   │   ├── zip_parser.py          # ZIP 解压 + middle.json / content_list.json
│   │   ├── images.py
│   │   └── naming.py
│   └── resources/review_rules/    # 规则源 + 自动构建的索引
│       ├── rules_test.md          # 规则源（用户维护）
│       ├── rules.faiss.json       # 纯 Python TF-IDF 回退索引
│       ├── rules.faiss            # FAISS 二进制索引（运行时构建）
│       ├── rules.faiss.meta.json  # FAISS 元数据
│       └── tfidf_vectorizer.pkl   # sklearn TfidfVectorizer
│
├── subagents/                     # 5 个子代理 AGENTS.md
│   ├── parser/AGENTS.md
│   ├── extractor/AGENTS.md
│   ├── reviewer/AGENTS.md
│   ├── research/AGENTS.md         # 检索（占位，尚未实现）
│   └── writer/AGENTS.md           # 生成（占位，尚未实现）
│
├── skills/                         # 4 个技能 SKILL.md
│   ├── standard-parsing/SKILL.md        # MinerU 解析触发
│   ├── standard-extraction/SKILL.md     # langextract 抽取触发
│   ├── standard-review/SKILL.md         # 标准审核触发
│   └── standard-drafting/SKILL.md       # 文档生成触发（占位，尚未实现）
│
├── memories/                       # 长期记忆种子（启动时灌入 Store）
│   ├── AGENTS.md
│   ├── preferences.md
│   └── project-notes.md
│
├── scripts/                        # 工程脚本
│   ├── __init__.py
│   ├── smoke_test.py
│   ├── final_smoke.py
│   └── rebuild_rules_index.py      # 手动触发规则知识库索引更新 CLI
│
├── tests/                          # 单元 / 集成测试（pytest）
│   ├── conftest.py
│   ├── test_*.py                   # 30+ 测试用例
│   └── ...
│
├── workspace/                      # 虚拟路径 /workspace 真实根目录
│   ├── input/{uploads,samples}
│   ├── output/{reviews,reports,metadata,mineru,drafts,artifacts}
│   ├── tmp/
│   └── templates/
│
├── design_docs/                    # 设计文档（Markdown）
├── pending_tools/                  # 待整合的脚本
├── .trae/commands/                 # Trae IDE 命令
├── .env.example
├── .gitignore
├── README.md                       
└── pyproject.toml
```

***

## 2. 安装与依赖

### 2.1 Python 环境要求

- Python ≥ 3.10（langgraph.json 指定 3.12；本地可用 3.10/3.11/3.12/3.13）
- Windows / macOS / Linux 均可；本仓库已在 Windows PowerShell 跑通

### 2.2 完整依赖（按 extras 分组）

| extras       | 包含                                                                                                                                                                   | 何时安装                  |
| ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------- |
| 核心（必装）       | `deepagents` / `langchain>=1.0` / `langchain-core` / `langgraph>=1.0` / `langsmith` / `langchain-qwq` / `langextract` / `python-dotenv` / `pyyaml` / `pydantic>=2.0` | 始终                    |
| `documents`  | `pypdf>=5.0.0` / `python-docx>=1.1.0`                                                                                                                                | 处理 PDF / DOCX 时       |
| `mineru`     | `requests>=2.31.0`                                                                                                                                                   | 调 MinerU HTTP 客户端     |
| `extraction` | `langextract`                                                                                                                                                        | 元数据抽取（核心也带）           |
| `review` ★   | `numpy` / `scikit-learn` / `python-docx` / `lxml` / `pymupdf` / `faiss-cpu` / `langchain-openai` / `openai`                                                          | **标准审核（FAISS）**       |
| `dev`        | `pytest>=8.0.0` / `ruff>=0.8.0` / `langgraph-cli[inmem]>=0.3.0`                                                                                                      | 跑测试 / 启 LangGraph dev |

### 2.3 Windows PowerShell 一键安装

```powershell
# 0. 创建并激活虚拟环境
python -m venv .venv
.\.venv\Scripts\Activate.ps1
# 如遇 PowerShell 执行策略拦截：
# Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned

# 1. 编辑模式安装主包 + 全部可选能力
pip install -e ".[documents,mineru,extraction,review,dev]"

# 2. 单独只装某个 extras（按需）
pip install -e ".[review]"            # 标准审核 FAISS 必备
pip install -e ".[documents,review]"  # 处理 PDF/DOCX + FAISS
```

> **关键提示**：`.[review]` 含 `faiss-cpu` 与 `scikit-learn`，必须装才能让
> `build_review_index` 写出 FAISS 三件套；不装也能跑（自动退到
> `rules.faiss.json` 纯 Python TF-IDF）。

### 2.4 关键包版本约束（来自 `pyproject.toml`）

```text
deepagents              # 任意最新版（Deep Agents 主框架）
langchain>=1.0,<2.0
langchain-core>=1.0,<2.0
langgraph>=1.0,<2.0
langsmith>=0.3.0
langchain-qwq           # 主对话模型（Qwen）
langextract             # 元数据抽取
pydantic>=2.0
faiss-cpu               # 标准审核 FAISS
scikit-learn            # TF-IDF 向量化器
pymupdf                 # PDF 解析（pymupdf>=1.24,<2）
python-docx             # Word 解析
lxml                    # XML / OOXML 处理
```

***

## 3. 配置（`config.yaml` + `.env`）

`config.yaml` 是**单一事实源**；`src/standard_document_assistant/config.py` 把它解析为强类型 dataclass。`.env` 仅覆盖敏感字段（API Key、Base URL、模型名）。

### 3.1 `config.yaml` 顶层结构

| 段                     | 关键字段                                                                                                     | 含义               |
| --------------------- | -------------------------------------------------------------------------------------------------------- | ---------------- |
| `app`                 | `name`, `default_language=zh-CN`                                                                         | Agent 名 / 默认中文回复 |
| `models.primary`      | `provider=qwen`, `class=langchain_qwq.ChatQwen`, `model=qwen3.7-plus`                                    | 主对话模型            |
| `runtime`             | `streaming=true`, `transport=sse`, `require_human_approval=true`                                         | 流式 + 审批          |
| `workspace`           | `uploads_dir`, `output_dir`, `allowed_upload_suffixes`                                                   | 虚拟目录与允许后缀        |
| `mineru`              | `api_mode=local/precise`, `precise_poll_interval=3`, `request_options`                                   | MinerU 解析        |
| `metadata_extraction` | `default_scope_mode`, `scoped_text_max_bytes`, `model=qwen3.7-plus`, `batch_length=40`, `max_workers=20` | langextract 抽取   |
| `standard_review`     | `rules_md`, `index_dir`, `top_k=8`, `max_review_rounds=2`, 30+ 字段                                        | 标准审核（详见 §5.5）    |
| `memory`              | `checkpointer=memory`, `store=inmemory`, `routes.{short_term,workspace,long_term}`                       | 记忆路由             |

### 3.2 关键环境变量（`.env`）

| 变量                                         | 用途                                                     | 备注                                   |
| ------------------------------------------ | ------------------------------------------------------ | ------------------------------------ |
| `DASHSCOPE_API_KEY`                        | LLM Judge / Metadata 抽取 / Embedding                    | Qwen 兼容 OpenAI 模式                    |
| `DASHSCOPE_BASE_URL`                       | 覆盖 `https://dashscope.aliyuncs.com/compatible-mode/v1` | 可选                                   |
| `DASHSCOPE_JUDGE_MODEL`                    | 覆盖 `standard_review.judge_model`                       | 默认 `qwen3.7-plus`                    |
| `DASHSCOPE_EMBEDDING_MODEL`                | 覆盖 `standard_review.embedding_model`                   | 默认 `text-embedding-v3`               |
| `STANDARD_DOC_LLM_OFFLINE_FALLBACK`        | LLM Judge 连接失败时启用本地保守降级                                | 仅本地联调使用；真实审核保持关闭                     |
| `MINERU_API_MODE`                          | `local` / `precise`                                    | 切 MinerU 客户端                         |
| `MINERU_API_BASE_URL`                      | local 模式服务地址                                           | `http://127.0.0.1:18001`             |
| `MINERU_API_TOKEN`                         | precise 模式 token                                       | 注意 90 天有效                            |
| `MINERU_REQUEST_TIMEOUT`                   | HTTP 超时（秒）                                             | 默认 600                               |
| `LANGSMITH_TRACING=true`                   | 开启 trace 上报                                            | 生产强烈建议                               |
| `LANGSMITH_API_KEY`                        | LangSmith API key                                      | 必需                                   |
| `LANGSMITH_PROJECT`                        | LangSmith 项目名                                          | 默认 `standard-document-assistant`     |
| `LANGGRAPH_API_URL`                        | FastAPI BFF 调用的 LangGraph Server 地址                    | 本地默认 `http://127.0.0.1:2024`         |
| `STANDARD_DOC_ENABLE_WORKSPACE_BACKEND`    | 是否启用 `/workspace/` 真实 Filesystem                       | 本地 `1`，LangGraph 部署 `0`              |
| `STANDARD_DOC_WORKSPACE_ROOT`              | 覆盖 `/workspace/` 的宿主机真实根目录                             | 可选；本地权限受限时指向可写目录                     |
| `STANDARD_DOC_ENABLE_LOCAL_SKILLS_BACKEND` | 是否启用 `/skills/` 真实 Filesystem                          | 默认 `1`                               |
| `STANDARD_DOC_ENABLE_HITL`                 | 主 Agent HITL 强制开关                                      | `langgraph dev` 默认关；生产/自建 API 设 `1`  |
| `STANDARD_DOC_ARTIFACT_API_BASE`           | 产物下载 API 前缀                                            | FastAPI 本地默认 `http://127.0.0.1:8080` |
| `STANDARD_DOC_EXPOSE_HOST_PATH`            | SSE/API 是否暴露 `host_path`                               | 本地调试设 `1`                            |

> **绝不**把任何 Key 写入 `config.yaml` 或提交到 Git。读取时全部走
> `os.getenv`；`StandardReviewConfig.judge_model` 也会优先看
> `DASHSCOPE_JUDGE_MODEL`（`config.py:343-346`）。

### 3.3 Windows 下从模板生成 `.env`

```powershell
Copy-Item .env.example .env
notepad .env   # 填入 DASHSCOPE_API_KEY / LANGSMITH_API_KEY / 等
```

***

## 4. 子智能体（5 个）— `subagents/`

主 Agent 通过 `task(...)` 委派，每个 subagent 都有独立的 `system_prompt` + 工具白名单 + 可选 `interrupt_on` + `skills`。

| 名称            | 主要工具                                                                                 | 何时调用                                | 关键约束                                                      |
| ------------- | ------------------------------------------------------------------------------------ | ----------------------------------- | --------------------------------------------------------- |
| **parser**    | `parse_file_with_mineru` + skill `standard-parsing`                                  | 用户上传 PDF/DOCX，缺 Markdown/manifest 时 | 强制 HITL；先返回 `virtual_md_path` + `cover_metadata`          |
| **extractor** | `extract_standard_metadata` + `validate_output_schema` + skill `standard-extraction` | 输入已是 Markdown / MinerU 路径时          | 不得先 `read_file` 预读全文；不得调用 `write_todos`；无需 SKILL 加载       |
| **reviewer**  | 6 个审核工具（含 `build_review_index`、`inspect_review_rules`）+ skill `standard-review`      | 进入审核流程                              | HITL 覆盖解析/审核/索引；返回 4 路径 + scope\_summary + audit\_summary |
| **research**  | 仅占位                                                                                  | 文档生成前的参考检索                          | 检索工具后续单独接入                                                |
| **writer**    | 仅占位                                                                                  | 草稿 / 报告 / 纪要起草                      | 写入需 HITL；产物路径使用 `/workspace/output/...`                   |

> 详细见 `subagents/<name>/AGENTS.md`。所有子代理都通过
> `build_subagents(langgraph_server=...)` 在
> [agent.py:257](file:///d:/deep-agents/src/standard_document_assistant/agent.py#L257)
> 集中注册。

***

## 5. 标准审核模块（Deep Agents 改造重点）

### 5.1 子图拓扑

```text
START → ingest → retrieve_rules → judge_rules → quality_gate (Command[Literal[...]])
                                                  │
            (insufficient_context & review_round < max_review_rounds)
                                                  ↓
                              widen_review_scope → reload_review_rules
                                                  └─────→ judge_rules (回环)
                                                                    │
                                                              (ok)  │
                                                                    ↓
                                                              format_review
                                                                    ↓
                                                                aggregate (scope_summary)
                                                                    ↓
                                                          write_outputs (audit_summary)
                                                                    ↓
                                                          write_manifest → END
```

- `quality_gate` 是 `Command[Literal["widen_review_scope", "format_review", "aggregate"]]` 节点：单次 return 既更新 state 也跳转。
- `widen_review_scope` → `reload_review_rules` → `judge_rules` 形成**多轮回环**，最大 `max_review_rounds` 轮。
- `state["trace_events"]`（`Annotated[list, operator.add]` 累加）+ `get_stream_writer` 双通道推送 `review.*` 事件。

### 5.2 LangGraph 子图注册

- 入口：[standard\_review\_graph.py](file:///d:/deep-agents/standard_review_graph.py) → `get_standard_review_graph()`
- 注册位置：[langgraph.json](file:///d:/deep-agents/langgraph.json) 的 `standard_review` 槽位
- Studio 可视化：`langgraph dev` 后浏览器打开 <http://localhost:2024> → 选择 `standard_review` 即可看到完整拓扑

### 5.3 关键节点职责

| 节点                    | 输入                                               | 关键产物                                                      | 实现                                                                  |
| --------------------- | ------------------------------------------------ | --------------------------------------------------------- | ------------------------------------------------------------------- |
| `ingest`              | `content_path` / `source_path` / `manifest_path` | `parsed_document`, `scope_text_map`, `active_scope_keys`  | 13-scope 切分（cover/toc/foreword/.../end），doc\_parser + serialization |
| `retrieve_rules`      | scope\_keys + 索引                                 | `section_rules`, `full_document_rules`, `retrieval_trace` | knowledge\_base + retriever                                         |
| `judge_rules`         | rules + 文档 + scope\_text\_map                    | `issues`, `insufficient_scopes`, `review_round`           | LLMSoftRuleJudge 4 策略 + `asyncio.gather` 并发                         |
| `quality_gate`        | 当前 issues + round                                | 跳转目标                                                      | `Command[Literal[...]]` 条件路由                                        |
| `widen_review_scope`  | 触发条件成立                                           | `partial_mode=full_document`                              | 重置 active\_keys                                                     |
| `reload_review_rules` | 全文激活 keys                                        | 重新 FAISS 检索                                               | 同 retrieve\_rules                                                   |
| `format_review`       | 源 PDF/DOCX                                       | 确定性格式 issue                                               | word\_parser + pdf\_format\_parser + format\_audit                  |
| `aggregate`           | issues                                           | `scope_summary` (按 (track, scope) 桶)                      | Counter 聚合                                                          |
| `write_outputs`       | 完整 state                                         | 4 份产物 + `audit_summary`                                   | reporter + audit\_summary                                           |
| `write_manifest`      | 4 份产物                                            | `*_review_manifest.json`                                  | ArtifactManifest                                                    |

### 5.4 双轨审核

| 轨                       | 来源              | 触发器                      | 工具                                     | 输出                                             |
| ----------------------- | --------------- | ------------------------ | -------------------------------------- | ---------------------------------------------- |
| **内容轨（content）**        | MinerU Markdown | `enable_llm_review=true` | `LLMSoftRuleJudge` 4 策略                | `AuditIssue(status=fail/insufficient_context)` |
| **格式轨（format\_source）** | 源 PDF/DOCX      | 提供 `source_path`         | `format_audit.run_format_source_audit` | `ReviewIssue(audit_track=format_source)`       |

### 5.4.1 默认审核、部分审核与单轨审核

`run_standard_review` 保留部分审核与单轨审核能力。默认行为不是一开始把全文作为单个块审核，而是：

- **Markdown only**：执行内容轨，默认 `partial_mode="sectional"`，按 13 个 scope 切分后审核；没有 PDF/DOCX `source_path` 时格式轨跳过。
- **Markdown + PDF/DOCX source\_path**：执行内容轨 + 格式轨。
- **质量门控扩大范围**：如果内容轨出现 `insufficient_context` 且 `review_round < max_review_rounds`，子图会自动进入 `full_document` 复审。
- **仅格式审核**：设置 `format_only=true` 或 `partial_mode="format_only"`，或直接调用 `run_format_source_review(source_path=...)`。
- **指定范围审核**：设置 `target_scopes`，例如 `["scope", "normative_references"]`。
- **指定行区间审核**：设置 `line_start` / `line_end`，仅对 Markdown 对应行片段构建审核视图。

常用 scope key 与中文别名：

| 中文             | scope key              |
| -------------- | ---------------------- |
| 范围             | `scope`                |
| 规范性引用文件 / 引用文件 | `normative_references` |
| 术语和定义          | `terms_definitions`    |
| 前言             | `foreword`             |
| 引言             | `introduction`         |
| 目次 / 目录        | `toc`                  |
| 附录             | `appendix`             |
| 参考文献           | `references`           |

在主 Agent 中可以用自然语言触发，例如“请仅审核 `/workspace/input/uploads/t/draft.md` 的范围和规范性引用文件部分”。系统具备 scope 别名映射，但该路径依赖主 Agent / reviewer 对自然语言的理解，**不适合作为另一个生成智能体的稳定机器接口**。生成工作流应使用 FastAPI 结构化接口传 `review_options.target_scopes=["scope","normative_references"]`，并在需要严格局部审核时设置 `disable_widen=true`，避免质量门控扩大到全文。

### 5.5 `standard_review` 段配置（30+ 字段）

```yaml
standard_review:
  # 规则源 / 索引目录
  rules_md: src/standard_document_assistant/resources/review_rules/rules_test.md
  index_dir: src/standard_document_assistant/resources/review_rules

  # 召回与回环
  top_k: 8
  max_review_rounds: 2
  auto_rebuild_index: true
  write_artifacts: true
  output_subdir: ""
  enable_llm_review: true
  scoped_text_max_chars: 12000

  # 报告摘要
  enable_audit_summary: true
  summary_model: qwen3.5-flash
  summary_max_chars: 600

  # LLM Judge
  judge_provider: dashscope-compatible
  judge_model: qwen3.5-flash          # env: DASHSCOPE_JUDGE_MODEL
  judge_base_url: ""                  # env: DASHSCOPE_BASE_URL
  judge_api_key_env: DASHSCOPE_API_KEY
  judge_temperature: 0.0
  judge_max_tokens: 2048
  judge_timeout: 60
  judge_max_retries: 2
  judge_max_workers: 4                # 异步并发 Semaphore

  # Embedding / 索引
  embedding_provider: dashscope
  embedding_model: text-embedding-v3  # env: DASHSCOPE_EMBEDDING_MODEL
  embedding_base_url: ""              # env: DASHSCOPE_BASE_URL
  embedding_dim: 1024
  embedding_api_key_env: DASHSCOPE_API_KEY

  # 上下文裁剪
  local_context_max_chars: 2200
  cross_section_max_chars: 3200
  window_max_chars: 2200
  window_overlap_chars: 160
  full_document_single_chars: 2200
  batch_window_max_rules: 4
  batch_window_max_chars: 3600
  batch_scope_max_rules: 4
  batch_scope_max_chars: 3600
  min_context_chars_local: 40
  min_context_chars_cross_section: 120
  min_context_chars_full_document: 400

  # 置信度降级
  low_confidence_floor: 0.35
```

### 5.6 规则库与索引（FAISS 三件套）

#### 5.6.1 三种产物

| 文件                      | 内容                                                         | 后端        |
| ----------------------- | ---------------------------------------------------------- | --------- |
| `rules_test.md`         | 规则源（用户维护）                                                  | —         |
| `rules.faiss.json`      | 纯 Python TF-IDF 序列化（含 rules / vectors / dim / terms / idf） | JSON 回退   |
| `rules.faiss`           | FAISS 二进制索引                                                | faiss-cpu |
| `rules.faiss.meta.json` | chunk\_id\_map + rules 字典                                  | faiss-cpu |
| `tfidf_vectorizer.pkl`  | scikit-learn `TfidfVectorizer`                             | faiss-cpu |

#### 5.6.2 加载策略（`load_knowledge_base` 主导）

`backend` 参数决定构建/加载顺序：

- `auto`（默认）：先 FAISS，缺包/缺文件时退到 JSON。
- `faiss`：仅走 FAISS（缺依赖时抛 `ImportError`）。
- `tfidf_json`：仅走 `rules.faiss.json`（纯 Python TF-IDF）。

#### 5.6.3 检索流程

`kb.search(query, scope, top_k, index_dir)`：

1. 若 `index_dir` 下三件套齐全 → `kb.search_faiss(...)` → `FaissVectorRetriever.search` → `IndexFlatIP` + L2 归一化 = 余弦。
2. 缺件套/缺包 → 退到 `kb.index` (TF-IDF JSON) → `search_faiss_or_tfidf` → 纯 Python 余弦。

#### 5.6.4 CLI：`scripts/rebuild_rules_index.py`

```powershell
# 默认 auto 后端，写到 config.yaml.standard_review.index_dir
python scripts/rebuild_rules_index.py

# 强制 FAISS（要求已 pip install -e ".[review]"）
python scripts/rebuild_rules_index.py --backend faiss --force-rebuild

# 仅 JSON 回退（无 faiss-cpu 时）
python scripts/rebuild_rules_index.py --backend tfidf_json

# 自定义路径
python scripts/rebuild_rules_index.py --rules-md custom.md --index-dir custom/index
```

输出会打印每件套是否存在、字节数、backend、source，便于运维审计。

#### 5.6.5 Deep Agents 工具

`build_review_index(force_rebuild=True, backend="auto")` —— 通过
[tools/review.py:694-756](file:///d:/deep-agents/src/standard_document_assistant/tools/review.py#L694-L756)
暴露，HITL 走 `reviewer` subagent 的 `interrupt_on`。返回值新增字段：

```json
{
  "status": "ok",
  "trace_id": "...",
  "rules_loaded": 3,
  "index_source": "rebuilt",
  "index_backend": "faiss",            
  "warnings": []                        // 缺包/失败时附带原因
}
```

### 5.7 LLM Judge 多策略

[LLMSoftRuleJudge](file:///d:/deep-agents/src/standard_document_assistant/review_core/llm_judge.py)：

| 策略              | 触发条件                           | 用法                 |
| --------------- | ------------------------------ | ------------------ |
| `single`        | `analysis_mode=local` 且 ctx 正常 | 1 rule + 1 context |
| `window`        | ctx 超 `batch_window_max_chars` | 1 rule + 窗口（含跨节）   |
| `cross_section` | `analysis_mode=cross_section`  | 多节上下文 + 跨节规则       |
| `full_document` | `analysis_mode=full_document`  | 全文 view + 全文规则     |

- **Send 并行**：`asyncio.gather` + `Semaphore(judge_max_workers)`；scope/rule 二维分组后并发扇出；异常隔离（`return_exceptions=True`）。
- **流式进度**：`get_stream_writer` 推送 `{"type": "scope_progress", "rule": chunk_id, "strategy": str}`。
- **置信度降级**：`confidence < low_confidence_floor` → `status=insufficient_context`。
- **解析失败保护**：`safe_json_loads` + `_empty_result` + `_fallback_issue`。

### 5.8 上下文构建

[DocumentContextBuilder](file:///d:/deep-agents/src/standard_document_assistant/review_core/context_chunker.py)：

- 按 scope 顺序构建 `_DocCache.ordered_units`（cover → toc → body → appendix → end）。
- 按 `target_scopes` 选取最相关 chunks，受 `local_context_max_chars` 截断。
- 提供 `structural_overview`（scope 长度分布）作为 LLM 输入。

### 5.9 报告摘要

[`generate_audit_summary`](file:///d:/deep-agents/src/standard_document_assistant/review_core/audit_summary.py)：

- 真实 LLM 模式：发 32 条以内 issues → JSON 4 字段（`summary` / `key_risks` / `top_fixes` / `insufficient`）。
- 离线回退：按 severity / scope 维度自动汇总，生成中英文混排的兜底摘要。

### 5.10 trace 与可观测

- 子图入口走 `invoke_traced_graph(graph_name=STANDARD_REVIEW_GRAPH_NAME, ...)`（[tracing.py:33](file:///d:/deep-agents/src/standard_document_assistant/tracing.py#L33)）。
- LangGraph Studio 中以独立 graph 呈现（不再是主 Agent 内部节点），可单独调 attempt、传 `thread_id`、查看 state 时间线。
- 节点级 trace 事件：每节点写一条 `{trace_id, job_id, component, node, event, status, created_at, ...}`。
- 端到端 smoke 脚本 [scripts/final\_smoke.py](file:///d:/deep-agents/scripts/final_smoke.py) 验证：build\_index → inspect → run\_standard\_review → validate\_review\_result\_schema 全部通过。

### 5.11 产物落盘

`/workspace/output/reviews/<job_id>/`（[pathing.review\_output\_root](file:///d:/deep-agents/src/standard_document_assistant/pathing.py)）：

| 文件                            | 内容                                           | 用途        |
| ----------------------------- | -------------------------------------------- | --------- |
| `<stem>_audit_report.md`      | 人类可读报告（Markdown）                             | 终端 / 前端展示 |
| `<stem>_audit_result.json`    | 结构化 issues + scope\_summary + audit\_summary | 下游系统消费    |
| `<stem>_audit_trace.json`     | 节点级 trace 事件 + 轮次 / 警告 / 路由                  | 调试与审计     |
| `<stem>_review_manifest.json` | `ArtifactManifest` 4 路径 + 警告                 | 跨系统溯源     |

***

## 6. MinerU 模块

### 6.1 双模对比

| 模式        | 端点                            | 适用        | 关键配置                                                                   |
| --------- | ----------------------------- | --------- | ---------------------------------------------------------------------- |
| `local`   | 自建 `/file_parse`（pipeline 模式） | 内网 / 隐私场景 | `request_options.parse_method=auto`，`formula_enable/table_enable=true` |
| `precise` | `mineru.net` 异步精准解析           | 公网高质量需求   | `precise_poll_interval=3`，`precise_poll_timeout=600`，`vlm` 模型          |

### 6.2 调用流程（[client.py](file:///d:/deep-agents/src/standard_document_assistant/integrations/mineru/client.py)）

```text
HTTP POST 任务接口  →  轮询 /extract/task/{task_id}  →  拿到 download_url
   ↓
下载 ZIP  →  zip_parser 解压
   ↓
images/  *.png / *.jpg   |   <stem>.md   |   middle.json / content_list.json（可选）
   ↓
naming.py 规范化路径；写入 /workspace/output/mineru/<job_id>/
   ↓
返回 virtual_md_path + cover_metadata + manifest
```

ZIP 文件名安全 + 重复任务跳过（`skip_if_zip_exists=true`）见 `zip_parser.py`。

### 6.3 `request_options` 关键字段

| 字段                    | 默认         | 说明                                   |
| --------------------- | ---------- | ------------------------------------ |
| `backend`             | `pipeline` | pipeline / vlm / hybrid              |
| `lang_list`           | `ch`       | 文档语种；中文为主                            |
| `parse_method`        | `auto`     | auto / ocr / txt                     |
| `formula_enable`      | `true`     | 启用公式识别                               |
| `table_enable`        | `true`     | 启用表格识别                               |
| `return_middle_json`  | `true`     | 输出 middle.json（覆盖 metadata / layout） |
| `return_content_list` | `true`     | 输出 content\_list.json（结构化块）          |
| `response_format_zip` | `true`     | 强制 ZIP 响应                            |

### 6.4 图片命名与 Markdown 引用改写

[integrations/mineru/naming.py](file:///d:/deep-agents/src/standard_document_assistant/integrations/mineru/naming.py) 读取 `content_list`，按：

1. 「图 x / 表 x」题注 → `images/figure_x.png`
2. 子图 `a）` / `b）` → `images/figure_x_a.png` / `_b.png`
3. 无题注 → 用 `middle_json` 顺序生成 `image_<idx>.png`

并改写 MD 中 `images/{hash}.jpg` 引用，确保与 `images/` 下文件名一致。

### 6.5 错误重试

`client.py` 集成 `RetryPolicy`：

- 429 / 5xx → 指数回退（`max_retries=2`）
- precise 模式轮询超时 → `precise_poll_timeout=600s` 兜底
- ZIP 损坏 → `zip_parser.bad_zip_handler` 报错到 `warnings`

### 6.6 跳过已解析

`skip_if_zip_exists=true` + `mineru.zip_archive_path` 已存在时直接复用，不重复扣 API 配额。

***

## 7. Langextract 元数据抽取模块

### 7.1 子图拓扑（[metadata\_extraction\_graph.py](file:///d:/deep-agents/metadata_extraction_graph.py)）

```text
parse_markdown → chunk_scope → extract_fields → validate → write_artifacts → END
```

- `parse_markdown`：按 `scope_mode=metadata`（截取到第 4 章前）或 `full`（全文）切片。
- `chunk_scope`：把长 Markdown 按 `max_char_buffer` 切成 chunk 列表。
- `extract_fields`：用 langextract 库 + DashScope Qwen 3.5-flash，对每个 chunk 并发抽字段。
- `validate`：必填字段（标准号 / 中文名称 / ICS / CCS / 层级 / 发布日期）缺失 → `quality_warnings`。
- `write_artifacts`：写 `<stem>_metadata.json` / `*_annotated.json` / `*_normalized.json` / `*_manifest.json`。

### 7.2 关键配置

```yaml
metadata_extraction:
  default_scope_mode: metadata
  scoped_text_max_bytes: 524288
  strict_validation: false
  write_artifacts: true
  model:
    provider: dashscope-compatible
    model: qwen3.5-flash
    base_url: ""
    timeout: 120
    max_retries: 2
    batch_length: 40
    max_workers: 20
    max_char_buffer: 1000
    extraction_passes: 1
```

### 7.3 抽取字段（来自 `tools/metadata.py._SUMMARY_KEYS`）

| 类别     | 字段                       |
| ------ | ------------------------ |
| **基础** | 标准号 / 标准中文名称 / 标准英文名称    |
| **分类** | ICS / CCS / 标准层级 / 标准性质  |
| **时间** | 发布日期 / 实施日期              |
| **单位** | 提出单位 / 归口单位 / 起草单位 / 起草人 |
| **替代** | 替代标准 / 被替代标准             |
| **引用** | 规范性引用文件 / 参考文献           |
| **术语** | 术语条目（数组）                 |

### 7.4 强约束

1. **禁止** `read_file` 预读全文（chunking 已自带；全文读会爆 LLM 上下文）。
2. **禁止** `edit_file` 改写 metadata JSON（疑似错误交由用户判断，工具内只产 `quality_warnings`）。
3. PDF / Word 必须先委派 `parser` subagent 调 `parse_file_with_mineru`。
4. 返回值通过 `aggregated_summary` + `quality_warnings` + `download` 字段汇报，不要二次校验。

### 7.5 引用材料

[skills/standard-extraction/references/](file:///d:/deep-agents/skills/standard-extraction/references)：

- [metadata-fields.md](file:///d:/deep-agents/skills/standard-extraction/references/metadata-fields.md) — 字段定义与判别依据
- [metadata-normalization.md](file:///d:/deep-agents/skills/standard-extraction/references/metadata-normalization.md) — 标准化（日期 / 编号）规则
- [quality-checklist.md](file:///d:/deep-agents/skills/standard-extraction/references/quality-checklist.md) — 字段质量自检清单

***

## 8. Skills（4 个）— `skills/`

参考官方[Skills](https://docs.langchain.com/oss/python/deepagents/skills)说明

Deep Agents `SkillsMiddleware` 启动时扫描 [`skills/`](file:///d:/deep-agents/skills)，按 `name` + `description` 命中时按需加载 SKILL.md 正文和 `references/` 配套材料；skills 在主 Agent 显式声明 `["/skills/"]`，在每个 subagent 显式绑定具体技能（**不**继承自主 Agent，参见 [`build_subagents`](file:///d:/deep-agents/src/standard_document_assistant/agent.py#L255-L313)）。每个 SKILL.md 的 frontmatter 统一为：

```yaml
---
name: <skill-name>
description: 触发条件 + 能力概述 + 边界（满足 Deep Agents SkillsMiddleware 按需加载的描述要求）
---
```

| 技能                                                                                | 触发场景                               | 关键内容                                                                         | Subagent    | 工具                                                                                                      | 业务子图                  |
| --------------------------------------------------------------------------------- | ---------------------------------- | ---------------------------------------------------------------------------- | ----------- | ------------------------------------------------------------------------------------------------------- | --------------------- |
| [standard-parsing](file:///d:/deep-agents/skills/standard-parsing/SKILL.md)       | 用户请求"解析 PDF / Word / 转换为 Markdown" | 输入路径约束、MinerU `local/precise` 模式对比、命名规则、image 改写、封面元信息                       | `parser`    | [`parse_file_with_mineru`](file:///d:/deep-agents/src/standard_document_assistant/tools/parser.py)      | —                     |
| [standard-extraction](file:///d:/deep-agents/skills/standard-extraction/SKILL.md) | 抽取标准号 / ICS / CCS / 层级等元数据         | 何时调 parser、何时直接 extractor、`scope_mode`、`cover_metadata_hint` 注入、产物 schema 校验 | `extractor` | [`extract_standard_metadata`](file:///d:/deep-agents/src/standard_document_assistant/tools/metadata.py) | `metadata_extraction` |
| [standard-review](file:///d:/deep-agents/skills/standard-review/SKILL.md)         | 用户请求"审核 / 审查 / 合规检查"               | 9 节点子图拓扑、5 步默认工作流、6 工具表、4 产物路径、索引与 trace 说明                                  | `reviewer`  | 5 个审核工具 ([`tools/review.py`](file:///d:/deep-agents/src/standard_document_assistant/tools/review.py))   | `standard_review`     |
| *[standard-drafting](file:///d:/deep-agents/skills/standard-drafting/SKILL.md)*   | *起草新标准 / 补写章节*                     | *澄清需求 → 检索 → 结构 → 草稿 → 自检（正式起草工具尚未接入）*                                       | *`writer`*  | *Deep Agents 内置* *`write_file`*                                                                         | *—*                   |

### 8.1 Skills 加载与挂载机制

1. **注册入口**：[`build_standard_document_agent`](file:///d:/deep-agents/src/standard_document_assistant/agent.py#L323-L398) 中 `agent_kwargs["skills"] = ["/skills/"]`；4 个具体技能在 [`build_subagents`](file:///d:/deep-agents/src/standard_document_assistant/agent.py#L255-L313) 内分别绑定到 `parser` / `extractor` / `reviewer` / `writer`。
2. **运行时文件后端**：[`build_backend`](file:///d:/deep-agents/src/standard_document_assistant/agent.py#L100-L162) 把 `/skills/` 路由到 `FilesystemBackend(root_dir=str(SKILLS_DIR.resolve()), virtual_mode=True)`；`STANDARD_DOC_ENABLE_LOCAL_SKILLS_BACKEND=0` 时切到 `StoreBackend`（部署模式默认开）。
3. **权限隔离**：[`build_permissions`](file:///d:/deep-agents/src/standard_document_assistant/agent.py#L143-L191) 显式 `deny: write /skills/**`，因此 SKILL.md 与 references **只读**，更新必须走仓库 PR。
4. **按需加载**：`SkillsMiddleware` 在 subagent 的 system\_prompt 中遇到相关任务时，按 frontmatter `name + description` 命中，注入 SKILL.md 正文；`references/*.md` 由 LLM 通过内置 `read_file` 按需 `cat`。
5. **与 system\_prompt 的关系**：[`prompts.py`](file:///d:/deep-agents/src/standard_document_assistant/prompts.py) 中 `PARSER_PROMPT` / `EXTRACTOR_PROMPT` / `REVIEWER_PROMPT` / `WRITER_PROMPT` 写明"使用 `<skill-name>` skill"，作为 SKILL.md 与工具调用之间的"行为契约"。

### 8.2 `standard-parsing`（MinerU PDF/DOCX 解析）

**职责**：将 `/workspace/input/uploads/**` 或 `/workspace/input/samples/**` 下的 PDF / DOCX 标准文档通过 MinerU 还原为 Markdown、图片、JSON 与 manifest。

**对应代码**：

- 工具：[`tools/parser.py`](file:///d:/deep-agents/src/standard_document_assistant/tools/parser.py) 中 `parse_file_with_mineru`，提供 sync + async 双实现（`StructuredTool.from_function(func=..., coroutine=...)`）。
- args\_schema：[`ParseFileWithMineruInput`](file:///d:/deep-agents/src/standard_document_assistant/tools/parser.py#L60-L92) Pydantic 校验业务字段；`runtime: ToolRuntime | None` 走 `InjectedToolArg`，**不**进入 prompt。
- 配置：[`config.yaml`](file:///d:/deep-agents/config.yaml) [的](file:///d:/deep-agents/config.yaml) [`mineru`](file:///d:/deep-agents/config.yaml) [段](file:///d:/deep-agents/config.yaml) 控制 `api_mode` / `api_base_url` / `request_options` / `skip_if_zip_exists` 等。
- Subagent：`parser` 在 [`build_subagents`](file:///d:/deep-agents/src/standard_document_assistant/agent.py#L265-L276) 注册，工具白名单仅 `parse_file_with_mineru`，HITL `allowed_decisions=["approve","edit"]`。

**frontmatter 摘要**：

- 触发：`/workspace/input/uploads/**` 或 `/workspace/input/samples/**` 下后缀为 `.pdf` / `.docx` 的标准文档。
- 边界：输入已是 Markdown / `content_list` / 已落盘到 `/workspace/output/mineru/**/*.md` 时**不要**再次解析，直接交给 `extractor` / `reviewer`。

**正文章节**：

1. "何时使用"——3 条触发条件 + 跳过条件。
2. "MinerU 调用模式"——`local`（自建 `/file_parse`，单次返回 ZIP）与 `precise`（mineru.net 上传→轮询→下载 ZIP）双模对比表。
3. "工作流"——6 步：`resolve_workspace_read_path` → 调 MinerU → 落 ZIP → 解析 ZIP → 写 manifest → 返回 `virtual_md_path` / `virtual_manifest_path` / `cover_metadata`。
4. "cover\_metadata 字段"——7 个字段（`standard_number` / `replaced_standard_number` / `ics` / `ccs` / `file_code` / `hierarchy_or_category` / `issuing_organizations`）的语义。
5. "产物"——`/workspace/output/mineru/{zip,md,images,json,manifests}/` 5 段目录。
6. "失败处理"——指向 `references/mineru-failures.md`。

**references/**：

- [pdf-parse-workflow.md](file:///d:/deep-agents/skills/standard-parsing/references/pdf-parse-workflow.md) — 6 步通用 PDF 解析工作流。
- [mineru-output-layout.md](file:///d:/deep-agents/skills/standard-parsing/references/mineru-output-layout.md) — local / precise 两种 ZIP 内目录树 + 统一处理要点。
- [mineru-failures.md](file:///d:/deep-agents/skills/standard-parsing/references/mineru-failures.md) — 配置 / 返回体 / 封面 / 图片 4 类失败的处置表。

**典型调用链**：

```text
主 Agent ──task──▶ parser subagent
                   ├─ /skills/standard-parsing        # SkillsMiddleware 按需加载
                   ├─ parse_file_with_mineru(file_path) # 工具（HITL 审批）
                   └─ 返回 { virtual_md_path, virtual_manifest_path, cover_metadata }
                       │
                       ▼
主 Agent ──task──▶ extractor subagent ─▶ extract_standard_metadata(file_path=virtual_md_path, cover_metadata_hint=cover_metadata)
                  或
                  reviewer subagent ─▶ run_standard_review(content_path=virtual_md_path, source_path=source_virtual)
```

### 8.3 `standard-extraction`（Markdown 元数据抽取）

**职责**：对 Markdown 形式的国标调用 langextract 子图抽取 19 个标准字段（ICS/CCS/标准号/层级/提出/归口/起草/引用/术语等），落盘为 `_metadata.json` / `_extraction.jsonl` / `_extraction.json` / `_manifest.json` 四件套。

**对应代码**：

- 工具：[`tools/metadata.py`](file:///d:/deep-agents/src/standard_document_assistant/tools/metadata.py) 中 `extract_standard_metadata`，对应 args\_schema [`ExtractStandardMetadataInput`](file:///d:/deep-agents/src/standard_document_assistant/tools/metadata.py#L55-L91)。
- 子图：[`graphs/metadata_extraction/graph.py`](file:///d:/deep-agents/src/standard_document_assistant/graphs/metadata_extraction/graph.py) 7 节点：`load_markdown → slice_scope → run_langextract → aggregate_fields → validate_schema → persist_output → write_manifest`。
- Subagent：`extractor` 在 [`build_subagents`](file:///d:/deep-agents/src/standard_document_assistant/agent.py#L278-L289) 注册，工具白名单 `[extract_standard_metadata, validate_output_schema]`，HITL 同上。

**frontmatter 摘要**：

- 触发：Markdown 虚拟路径（`/workspace/output/mineru/**/*.md` 或 `/workspace/input/uploads/**/*.md`）。
- 边界：PDF/Word 必须先经 `parser` → `parse_file_with_mineru`；不要 `read_file` 预读全文；不要 `edit_file` 改写元数据 JSON；不要为了常规抽取读取本 skill。

**正文章节**：

1. "何时使用"——3 条触发条件。
2. "Instructions"——8 条强约束：必走 `extract_standard_metadata`；`scope_mode` 默认 `metadata`（截到第 4 章前）；`cover_metadata_hint` 不一致时仅产 `quality_warnings`；不要二次校验；大段原文不要回灌主 Agent。
3. "输出"——`/workspace/output/metadata/{json,annotated,normalized,manifests}/` 4 段目录；`download.host_path` 与 `download.download_url`（依赖 `STANDARD_DOC_ARTIFACT_API_BASE`）。

**references/**：

- [metadata-fields.md](file:///d:/deep-agents/skills/standard-extraction/references/metadata-fields.md) — 19 个目标字段 + "缺失字段保持空值/空列表，禁止编造"。
- [metadata-normalization.md](file:///d:/deep-agents/skills/standard-extraction/references/metadata-normalization.md) — 编号（`GB/T` / `GB/Z`）、ICS（数字点分）、CCS（大写字母+2 位数字）、数组化（提出/归口/起草/起草人）、`制修订` 三选一。
- [quality-checklist.md](file:///d:/deep-agents/skills/standard-extraction/references/quality-checklist.md) — 5 条质检清单。

**典型调用链**：

```text
主 Agent ──task──▶ parser ──parse_file_with_mineru──▶ { virtual_md_path, cover_metadata }
                                                              │
                                                              ▼
主 Agent ──task──▶ extractor ──extract_standard_metadata(file_path=virtual_md_path,
                                                          cover_metadata_hint=cover_metadata,
                                                          scope_mode="metadata")──▶
   { aggregated_summary, quality_warnings, download, virtual_output_path, manifest_virtual_path }
```

### 8.4 `standard-review`（双轨审核 + 范围扩大回环）

**职责**：以 `standard_review` LangGraph 子图为核心，做"内容轨（FAISS RAG + LLM Judge 多策略）"与"格式轨（确定 DOCX/PDF 解析）"双轨审核，并在 `insufficient_context` 时触发 `widen_review_scope → reload_review_rules → judge_rules` 多轮回环，最后产出 `report / result / trace / manifest` 4 份产物。

**对应代码**：

- 工具：[`tools/review.py`](file:///d:/deep-agents/src/standard_document_assistant/tools/review.py) 暴露 5 个工具（`run_standard_review` / `run_format_source_review` / `inspect_review_rules` / `build_review_index` / `validate_review_result_schema`），全部用 Pydantic `args_schema` 校验。
- 子图：[`graphs/standard_review/graph.py`](file:///d:/deep-agents/src/standard_document_assistant/graphs/standard_review/graph.py) 9 节点：`ingest → retrieve_rules → judge_rules → quality_gate (Command[Literal[...]]) → (widen|ok) → format_review → aggregate → write_outputs → write_manifest`。
- 知识库：[`review_core/knowledge_base.py`](file:///d:/deep-agents/src/standard_document_assistant/review_core/knowledge_base.py) + [`review_core/retrievers/vector_retriever.py`](file:///d:/deep-agents/src/standard_document_assistant/review_core/retrievers/vector_retriever.py)；缺 `faiss-cpu` 时退到 `rules.faiss.json` 纯 Python TF-IDF。
- Subagent：`reviewer` 在 [`build_subagents`](file:///d:/deep-agents/src/standard_document_assistant/agent.py#L291-L313) 注册，6 个工具 + 1 skill；`interrupt_on` 覆盖 `parse_file_with_mineru` / `run_standard_review` / `run_format_source_review` / `build_review_index`，`allowed_decisions=["approve","edit"]`。

**frontmatter 摘要**：

- 触发：审核 / 审查 / 合规检查；`/workspace/output/mineru/**/*.md` 或 `/workspace/input/uploads/**/*.md`。
- 边界：PDF/Word 必须先经 `parser` → `parse_file_with_mineru`；**审核本身不要先调** **`extract_standard_metadata`**；流式事件命名空间 `<domain>.<stage>`（`review.ingest.*` / `review.retrieve.*` / `review.judge.*` / `review.quality_gate.*` / `review.widen.*` / `review.format.*` / `review.aggregate.*` / `review.report.*` / `review.manifest.*`）。

**正文章节**：

1. "Overview"——双轨审核（内容轨 / 格式轨）+ 子图拓扑 ASCII 图。
2. "Instructions"——10 条强约束：优先 `run_standard_review`；PDF/Word 必须先解析；改 `rules_test.md` / 切换 embedding 后必先 `build_review_index`；`inspect_review_rules` 预览；**不要**把 `extract_standard_metadata` 作为固定前置；路径必走 `/workspace/`；所有发现必带 `chunk_id` + `source_ref` + 严重级别 + 状态 + 策略 + 建议；**不得伪造**条款或来源；只摘要关键问题。
3. "Tool Set"——5 工具表（含 HITL 标注）。
4. "Default Workflow"——5 步：确认文件 → 必要时解析 → 可选预览 → 调 `run_standard_review` → 调 `validate_review_result_schema`。
5. "Artifact Layout"——`/workspace/output/reviews/<job_id>/` 4 份产物。
6. "Knowledge Base"——`rules_test.md` + 自动构建 `rules.faiss.json` + FAISS 缺包时回退纯 Python TF-IDF。
7. "Trace & Resumption"——`invoke_traced_graph` 透传父 callbacks；`langgraph dev` Studio 中以 `standard_review` 节点呈现；`review_round` / `widened` 标记；`MemorySaver` / PostgresSaver 检查点。

**典型调用链**：

```text
主 Agent ──task──▶ reviewer subagent
                   ├─ /skills/standard-review
                   ├─ (缺失 Markdown) parse_file_with_mineru         # HITL
                   ├─ (可选)         inspect_review_rules
                   ├─ (改规则后)     build_review_index               # HITL
                   ├─                run_standard_review              # HITL
                   │     content_path / source_path / manifest_path / target_scopes / partial_mode
                   └─                validate_review_result_schema
                   └─ 返回 { report/result/trace/manifest 4 路径,
                            scope_summary, audit_summary, retrieval_trace,
                            review_round, widened, warnings }
```

### 8.5 `standard-drafting`（标准文档起草 — **占位 / 尚未实现**）

> **⚠ 重要声明**：`standard-drafting` 当前**仅作为占位与设计契约存在，尚未在当前架构中真正实现**：
>
> - 没有专属业务工具（`src/standard_document_assistant/tools/` 内无 `drafting.py`）；
> - `writer` subagent 仅持有 `validate_output_schema` 一个工具，主写入动作走 Deep Agents 内置 `write_file`；
> - [`subagents/writer/AGENTS.md`](file:///d:/deep-agents/subagents/writer/AGENTS.md) 显式注明"正式起草工具尚未接入"；
> - `langgraph.json` 中**未**注册 `drafting` 子图，`build_standard_document_agent` 中**没有**专门的起草 LangGraph 节点；
> - 因此本节描述的是**设计与预期行为**，实际能力仅停留在"用 `write_file` 写一份 Markdown 草稿"。
>
> 待未来接入真正的起草工具（候选：模板驱动的 RAG 生成、参考资料检索 + 章节规划、风格约束校验）后，再补齐 frontmatter、references、专属工具和 HITL 策略。

**预期职责（设计态）**：根据用户需求、参考资料和模板起草新标准 / 补写章节的 Markdown 草稿，产物落 `/workspace/output/drafts/`，不编造引用依据。

**对应代码（占位态）**：

- 工具：暂无业务工具；subagent 的 `tools=[validate_output_schema]`，主写入动作走内置 `write_file`。
- Subagent：`writer` 在 [`build_subagents`](file:///d:/deep-agents/src/standard_document_assistant/agent.py#L302-L310) 注册，`skills=[drafting_skill]`，HITL 由主 Agent `interrupt_on["write_file"]` 兜底。
- 产物目录：[`constants.DRAFTS_OUTPUT_DIR = OUTPUT_DIR / "drafts"`](file:///d:/deep-agents/src/standard_document_assistant/constants.py)，对应 `/workspace/output/drafts/`。

**frontmatter 摘要**：

- 触发：用户要求起草新标准 / 补写标准章节 / 根据需求生成标准文档草稿。
- 边界：（建议在 frontmatter 中补）"**当前未实现**；正式起草工具尚未接入；产物落 `/workspace/output/drafts/`；不编造引用依据"。

**正文章节**：当前仅 5 条 Instructions（澄清需求 → 整理信息不足时列出待确认问题 → 默认结构（标题/前言/范围/规范性引用/术语/技术要求/验证/实施）→ 引用资料必须标注来源 → 输出 Markdown 草稿）。**该 SKILL.md 目前最薄**（14 行），且与实际代码能力一致——因为功能本身未实现。

**预期调用链（设计态，未完全实现）**：

```text
主 Agent ──task──▶ writer subagent
                   ├─ /skills/standard-drafting
                   ├─ (信息不足) 列待确认问题
                   ├─ (信息充分) 起草 Markdown 草稿
                   ├─ write_file(/workspace/output/drafts/<stem>.md)   # HITL (主 Agent 层)
                   └─ 返回产物虚拟路径 + 关键章节摘要 + 引用来源
```

**与其它 3 个技能的关键差异**：

| 维度           | standard-parsing / standard-extraction / standard-review | standard-drafting             |
| ------------ | -------------------------------------------------------- | ----------------------------- |
| 是否实现         | ✅ 完整实现 + 业务子图                                            | ❌ **占位 / 未实现**                |
| 业务工具         | 有（1 / 1 / 5 个）                                           | 无（仅 `validate_output_schema`） |
| LangGraph 子图 | 2 个（metadata\_extraction / standard\_review）             | 0 个                           |
| HITL 策略      | 工具级 + subagent 级 interrupt\_on                           | 仅 `write_file` 主 Agent 级      |
| references/  | 3 / 3 / 0 篇                                              | 0 篇                           |
| 适用生产         | ✅ 可用                                                     | ⚠️ 只能产出"草稿骨架"，复杂起草需等专门工具接入    |

### 8.6 引用关系图

```text
AGENTS.md (主 Agent 工作约束)
    │
    ▼
build_standard_document_agent()
    ├─ skills=["/skills/"]                           # SkillsMiddleware 入口
    │     ├─ /skills/standard-parsing/SKILL.md       ── /skills/standard-parsing/references/*.md
    │     ├─ /skills/standard-extraction/SKILL.md    ── /skills/standard-extraction/references/*.md
    │     ├─ /skills/standard-review/SKILL.md
    │     └─ /skills/standard-drafting/SKILL.md
    │
    ├─ subagents = build_subagents()
    │     ├─ parser   → tools=[parse_file_with_mineru]              → skills=[standard-parsing]
    │     ├─ extractor→ tools=[extract_standard_metadata, …]         → skills=[standard-extraction]
    │     ├─ reviewer → tools=[run_standard_review, …(5)]            → skills=[standard-review]
    │     ├─ research → tools=[]                                     (无 skill；占位)
    │     └─ writer   → tools=[validate_output_schema]               → skills=[standard-drafting]
    │
    ├─ tools (主 Agent 层)
    │     ├─ validate_output_schema                                  (通用 schema 校验)
    │     └─ propose_memory_update                                   (长期记忆提案)
    │
    ├─ backend = CompositeBackend(
    │       "/skills/"   → FilesystemBackend(root=skills/),
    │       "/memories/" → StoreBackend(namespace=…),
    │       "/workspace/"→ FilesystemBackend(root=workspace/),
    │       default      → StateBackend()
    │   )
    │
    └─ permissions = build_permissions()       # /skills/** 只读；/workspace/input/** 写禁止
```

### 8.7 在主 Agent 提示词中的引用

[`prompts.py`](file:///d:/deep-agents/src/standard_document_assistant/prompts.py) 中 5 个 system\_prompt 显式引用了 skills：

| Prompt                                                                                           | 引用的 skill             | 引用形式                                                                 |
| ------------------------------------------------------------------------------------------------ | --------------------- | -------------------------------------------------------------------- |
| [`MAIN_SYSTEM_PROMPT`](file:///d:/deep-agents/src/standard_document_assistant/prompts.py#L3-L43) | —                     | 不直接引用 skills，而是通过 `task(...)` 委派 subagent，subagent 自行加载 skill        |
| [`PARSER_PROMPT`](file:///d:/deep-agents/src/standard_document_assistant/prompts.py#L29)         | `standard-parsing`    | "使用 standard-parsing skill，并调用 parse\_file\_with\_mineru"            |
| [`EXTRACTOR_PROMPT`](file:///d:/deep-agents/src/standard_document_assistant/prompts.py#L37-L45)  | `standard-extraction` | "若收到 PDF/Word 路径…应请主 Agent 先委派 parser 完成 MinerU 解析"（隐式委派 + skill 约束） |
| [`REVIEWER_PROMPT`](file:///d:/deep-agents/src/standard_document_assistant/prompts.py#L31)       | `standard-review`     | "使用 standard-review skill 的规则处理审核任务"                                 |
| [`WRITER_PROMPT`](file:///d:/deep-agents/src/standard_document_assistant/prompts.py#L33)         | `standard-drafting`   | "使用 standard-drafting skill，先整理需求和缺口，再生成 Markdown 草稿"                |
| [`RESEARCH_PROMPT`](file:///d:/deep-agents/src/standard_document_assistant/prompts.py#L47)       | —                     | "当前没有正式检索工具"（无 skill 绑定）                                             |

> **设计原则**：主 Agent 的 system\_prompt **不直接列** skills（避免 LLM 误以为所有任务都要走 skill），而是让 subagent 在自己的 system\_prompt 中显式声明 skill 依赖。`SkillsMiddleware` 根据 description 命中时只把"对应 subagent 的 skill"注入到该 subagent 的上下文。

***

## 9. 工具集（Tools）

[src/standard\_document\_assistant/tools/__init__.py](file:///d:/deep-agents/src/standard_document_assistant/tools/__init__.py) 统一导出：

| 工具                                                       | 用途                             | HITL |
| -------------------------------------------------------- | ------------------------------ | ---- |
| `parse_file_with_mineru(file_path, ...)`                 | PDF/DOCX → Markdown + manifest | ✅    |
| `extract_standard_metadata(file_path \| markdown, ...)`  | langextract 元数据抽取              | ✅    |
| `run_standard_review(content_path, source_path?, ...)`   | 端到端审核                          | ✅    |
| `run_format_source_review(source_path, ...)`             | 仅跑格式轨                          | ✅    |
| `inspect_review_rules(query, scope?, top_k=5)`           | FAISS 检索预览                     | —    |
| `build_review_index(force_rebuild=True, backend="auto")` | 构建/重建索引                        | ✅    |
| `validate_review_result_schema(result_path)`             | 校验产物 schema                    | —    |
| `validate_output_schema(payload, schema_name)`           | 通用 schema 校验                   | —    |
| `propose_memory_update(target, content)`                 | 长期记忆**提案**（不直接写 Store）         | ✅    |

`PARSER_TOOLS` / `METADATA_TOOLS` / `REVIEW_TOOLS` / `STANDARD_DOCUMENT_TOOLS` 四组用于 subagent 工具白名单。

***

## 10. 主 Agent 与 middleware 整合

[build\_standard\_document\_agent](file:///d:/deep-agents/src/standard_document_assistant/agent.py#L323-L398) 通过 `create_deep_agent` 风格组装：

```python
agent = build_standard_document_agent(strict_model=True, langgraph_server=False)
# 等价于:
# create_deep_agent(
#     model=qwen3.7-max,
#     tools=STANDARD_DOCUMENT_TOOLS,
#     subagents=[parser, extractor, reviewer, research, writer],
#     skills=["/skills/"],   # 按需加载
#     backend=CompositeBackend(  # 详见 §10.2
#         default=StateBackend(),
#         routes={
#             "/memories/": StoreBackend(namespace=...),
#             "/skills/":   FilesystemBackend(root=skills_dir, virtual_mode=True),
#             "/workspace/": FilesystemBackend(root=workspace_dir, virtual_mode=True),
#         },
#     ),
#     permissions=build_permissions(),
#     interrupt_on={...9 工具...},
#     checkpointer=MemorySaver(),
#     store=InMemoryStore(),
#     memory=["/memories/AGENTS.md", "/memories/preferences.md"],
#     response_format=AgentResult,
# )
```

### 10.1 `build_permissions()` 落点约束

- **deny**：所有 `*.env*` / `*secret*`、原始输入 `/workspace/input/**` 写。
- **allow (read)**：`/workspace/input/**`、`/workspace/templates/**`、`/skills/**`、`/memories/**`、产物 `/workspace/output/**`、`/workspace/tmp/**`。
- **deny (write)**：`/workspace/input/**`、`/workspace/templates/**`、`/skills/**`、`/memories/**`。
- **核心约束**：
  - 原始输入不可被 Agent 覆盖
  - 产物写入 `output/` 或 `tmp/`
  - `/memories/` 只读；更新走 `propose_memory_update` + HITL

### 10.2 CompositeBackend 路由

| 路径前缀          | 后端                                      | 说明                                                                     |
| ------------- | --------------------------------------- | ---------------------------------------------------------------------- |
| `/memories/`  | `StoreBackend`                          | 长期记忆；namespace 按 `langgraph_server=True` 时切到 `(assistant_id, user_id)` |
| `/skills/`    | `FilesystemBackend` (root=`skills/`)    | 按需加载 SKILL.md；`STANDARD_DOC_ENABLE_LOCAL_SKILLS_BACKEND=0` 时切 Store    |
| `/workspace/` | `FilesystemBackend` (root=`workspace/`) | 输入 / 产物真实落点；LangGraph 部署默认关                                            |
| 默认            | `StateBackend`                          | 短期 scratch，仅在 thread 内可见                                               |

### 10.3 HITL（Human-in-the-Loop）

| 工具                                                 | 触发原因         | 触发位置                         |
| -------------------------------------------------- | ------------ | ---------------------------- |
| `write_file` / `edit_file`                         | 写产物          | 主 Agent                      |
| `execute`                                          | 执行命令         | 主 Agent                      |
| `parse_file_with_mineru`                           | 调用 MinerU 解析 | 主 Agent + parser subagent    |
| `extract_standard_metadata`                        | LLM 抽取       | 主 Agent + extractor subagent |
| `run_standard_review` / `run_format_source_review` | 审核           | 主 Agent + reviewer subagent  |
| `build_review_index`                               | 重建 FAISS     | 主 Agent + reviewer subagent  |
| `propose_memory_update`                            | 长期记忆         | 主 Agent                      |

所有 HITL 触发后通过 `Command(resume={"decisions": [{"type": "approve" | "reject" | "edit", ...}]})` 恢复。

> **生产 vs 开发**：`langgraph dev` 默认关闭 HITL（子图 resume 易传错格式导致
> `TypeError`）。生产/自建 API 审批流可设 `STANDARD_DOC_ENABLE_HITL=1`。

***

## 11. Stream 设计

### 11.1 双通道

1. **state\["trace\_events"]**：`Annotated[list, operator.add]` 累加，节点 return dict 时合并。
2. **get\_stream\_writer**：直接向前端 SSE 推送，与 state 解耦。

### 11.2 命名空间统一 `<domain>.<stage>`

| domain          | 触发源            | 示例事件                                                                                                                                                                                         |
| --------------- | -------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `mineru.*`      | MinerU 解析      | `mineru.start` / `mineru.progress` / `mineru.end`                                                                                                                                            |
| `meta.*`        | langextract 抽取 | `meta.parse` / `meta.extract` / `meta.aggregate`                                                                                                                                             |
| `review.*`      | 标准审核           | `review.ingest.*` / `review.retrieve.*` / `review.judge.*` / `review.quality_gate.*` / `review.widen.*` / `review.format.*` / `review.aggregate.*` / `review.report.*` / `review.manifest.*` |
| `review.tool.*` | 工具层包装          | `review.tool.start` / `review.tool.end`                                                                                                                                                      |

### 11.3 前端消费

`/api/threads/{thread_id}/runs/stream` SSE 推送 → 前端按 `<domain>` 分通道渲染进度。

### 11.4 FastAPI BFF（本地 Phase 1）

当前已提供本地 FastAPI 代理后端，用于在 `langgraph dev` 外层补齐业务接口：

- 文件上传：保存到 `/workspace/input/uploads/{thread_id}/`，返回 Deep Agents 虚拟路径。
- 标准审核流式入口：调用 LangGraph Server 上游 `agent`，以 SSE 返回 `run.started`、`agent.progress`、`approval.required`、`artifact.created`、`run.completed` 等事件。
- 结构化标准审核入口：直接调用上游 `standard_review` 图，支持非流式返回审核报告内容和结果 JSON，适合“生成标准文档”工作流循环调用。
- HITL 恢复：通过 `Command(resume={"decisions": [...]})` 恢复暂停的 run。
- 产物列表与下载：按 thread 登记并下载审核报告、结果 JSON、trace、manifest 等产物。

FastAPI 入口：

```powershell
uvicorn standard_document_assistant.api.app:app --host 0.0.0.0 --port 8080 --reload
```

本地审核的典型调用链：

```text
POST /api/threads
POST /api/threads/{thread_id}/uploads
POST /api/threads/{thread_id}/standard-review/stream
POST /api/threads/{thread_id}/runs/resume        # 如 SSE 出现 approval.required
GET  /api/threads/{thread_id}/artifacts
GET  /api/threads/{thread_id}/artifacts/{artifact_id}/download
```

生成标准工作流应优先使用结构化非流式入口，避免依赖主 Agent 对自然语言的参数推断：

```text
POST /api/threads
POST /api/threads/{thread_id}/uploads
POST /api/review-jobs/standard-review
```

`POST /api/review-jobs/standard-review` 与 `POST /api/review-jobs/standard-review/stream` 均支持 `review_options`：

- `mode="content_only"`：默认内容审核，按章节分轨。
- `mode="content_and_format"`：内容审核 + 格式审核，需要同时提供原始 `source_path`。
- `mode="format_only"`：仅格式审核，不进行内容规则判断。
- `mode="scoped_content"`：仅审核指定章节，传 `target_scopes`，例如 `["scope","normative_references"]`。
- `mode="line_range_content"`：仅审核 Markdown 指定行号范围，传 `line_start` / `line_end`。
- `mode="full_document_content"`：全文内容审核。

严格部分审核时建议传 `disable_widen=true`，否则标准审核子图的质量门控在依据不足时可能扩大到全文补充审核。非流式结构化入口会返回 `passed`、`review_report_markdown`、`review_result` 和产物下载信息，适合作为“生成 → 审核 → 修改 → 再审核”循环的判断输入。

完整接口文档见：[design\_docs/FASTAPI\_BFF\_PHASE1\_API.md](file:///d:/deep-agents/design_docs/FASTAPI_BFF_PHASE1_API.md)。

***

## 12. Send 并行化

| 位置                                | 模式                                                | 触发                                        |
| --------------------------------- | ------------------------------------------------- | ----------------------------------------- |
| `LLMSoftRuleJudge.run_dual_route` | `asyncio.gather` + `Semaphore(judge_max_workers)` | `judge_rules` 节点对 (scope, rule) 二维分组后并发扇出 |
| `extract_standard_metadata` 子图    | `langextract` 库内并发                                | chunk 间并发抽字段                              |
| `parse_file_with_mineru` 客户端      | `requests` 异步 + `RetryPolicy`                     | 网络瞬断自动重试                                  |

> **未来扩展**：当 scope 数量与规则数量均 > 100 时，建议改造为
> `langgraph.types.Send` + `StateGraph.add_conditional_edges("ingest", ..., [Send("judge_scope", ...), ...])`
> 显式扇出，进一步压低 `judge_rules` 节点延迟。

***

## 13. 多用户 runtime

### 13.1 当前实现（单进程内隔离）

[agent.py:69-95](file:///d:/deep-agents/src/standard_document_assistant/agent.py#L69-L95)：

```python
def _memory_namespace_factory(*, langgraph_server: bool = False):
    def _namespace(rt: Any) -> tuple[str, ...]:
        if langgraph_server:
            server_info = getattr(rt, "server_info", None)
            if server_info is not None:
                assistant_id = getattr(server_info, "assistant_id", AGENT_NAME)
                user = getattr(server_info, "user", None)
                user_id = getattr(user, "identity", None) if user is not None else None
                if user_id:
                    return (assistant_id, user_id)
                return (assistant_id,)
        return MEMORY_NAMESPACE
    return _namespace
```

`langgraph_server=True` 时 namespace 切到 `(assistant_id, user_id)`；否则用全局 `MEMORY_NAMESPACE`。

### 13.2 LangGraph Server 部署模式

- `build_standard_document_agent(langgraph_server=True)`：不显式挂 `checkpointer` / `store`，由 LangGraph Server 注入平台托管。
- `STANDARD_DOC_ENABLE_WORKSPACE_BACKEND=0`：禁用 `/workspace/` 真实 Filesystem（平台托管）。
- `STANDARD_DOC_ENABLE_LOCAL_SKILLS_BACKEND=1`（默认）：保留 `/skills/` 真实 Filesystem 便于热更新。

### 13.3 多用户共享与隔离

- **共享**：助手级资产（`/skills/`, subagent system\_prompts）放 `StoreBackend` 走 `(assistant_id,)` namespace。
- **隔离**：长期记忆 `/memories/` 走 `(assistant_id, user_id)` namespace。
- **配置**：每个 thread 携带 `configurable.thread_id`（LangGraph 内置）+ `configurable.user_id`（自建，可选）。
- **审计**：`invoke_traced_graph` 的 metadata 携带 `parent_agent` / `tool_call_id`，LangSmith 端可按 user\_id 过滤。

***

## 14. Trace & LangSmith 可视化

### 14.1 三层 trace

| 层          | 触发点                                           | 内容                                                                   |
| ---------- | --------------------------------------------- | -------------------------------------------------------------------- |
| L1 主 Agent | `create_deep_agent` 内置                        | 主图节点 + subagent 委派链                                                  |
| L2 子图      | `invoke_traced_graph`                         | `metadata_extraction` / `standard_review` 拓扑                         |
| L3 节点级     | `state["trace_events"]` + `get_stream_writer` | 每节点 `{trace_id, job_id, component, node, event, status, created_at}` |

### 14.2 Studio 可视化（`langgraph dev`）

```powershell
pip install -e ".[dev]"      # 含 langgraph-cli[inmem]
langgraph dev                # 默认 http://localhost:2024
```

Studio 中可看到：

- 左侧 Graph 列表：`agent` / `metadata_extraction` / `standard_review`
- 点击 `standard_review` → 完整 9 节点拓扑 + `Command[Literal[...]]` 边
- 右侧可发起 attempt（选 input schema）、看 state 时间线、Replay / Fork

### 14.3 推荐环境变量

```text
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv2_...
LANGSMITH_PROJECT=standard-document-assistant
```

### 14.4 节点级 trace 事件示例

```json
{"type": "review.retrieve.rules", "trace_id": "trace_xxx", "status": "success", "section_rules": 3, "full_rules": 1, "active_scopes": ["scope", "normative_references"]}
{"type": "review.judge.start", "rule": "SR-P0-001", "strategy": "single", "context_chars": 880}
{"type": "review.judge.end", "rule": "SR-P0-001", "status": "fail", "confidence": 0.92, "duration_ms": 1240}
{"type": "review.quality_gate.route", "decision": "widen_review_scope", "insufficient_scopes": ["scope"]}
{"type": "review.format.issue", "rule": "format_chapter", "severity": "major"}
{"type": "review.aggregate.summary", "scope_summary": {...}}
{"type": "review.report.written", "report": "/workspace/output/reviews/.../audit_report.md"}
```

***

## 15. 安装与运行（端到端）

```powershell
# 0. 创建并激活虚拟环境（建议 Python 3.12，pyproject 已锁定）
python -m venv .venv
.\.venv\Scripts\Activate.ps1
# Windows PowerShell 执行策略拦截时：
# Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned

# 1. 完整安装（含 FAISS / MinerU / langextract / dev）
pip install -e ".[documents,mineru,extraction,review,dev]"

# 2. 配置环境变量
Copy-Item .env.example .env
notepad .env
# 必填：DASHSCOPE_API_KEY、LANGSMITH_API_KEY
# 选填：MINERU_API_TOKEN（precise 模式）
# FastAPI BFF 本地联调建议：
# LANGGRAPH_API_URL=http://127.0.0.1:2024
# STANDARD_DOC_ARTIFACT_API_BASE=http://127.0.0.1:8080
# 交互式审批测试可设置 STANDARD_DOC_ENABLE_HITL=1
# 生成工作流自动循环建议设置 STANDARD_DOC_DISABLE_HITL=1，且不要设置 STANDARD_DOC_ENABLE_HITL=1
# STANDARD_DOC_ENABLE_WORKSPACE_BACKEND=1
# 如服务进程无法写入 D:\deep-agents\workspace，可设置到一个可写目录：
# STANDARD_DOC_WORKSPACE_ROOT=C:\Users\32084\AppData\Local\Temp\deep_agents_workspace

# 3. 构建审核规则索引（首次 / 改 rules_test.md 后）
python scripts/rebuild_rules_index.py --force-rebuild --backend auto

# 4. 端到端 smoke test
python scripts/final_smoke.py

# 5. 跑 pytest 单元 / 集成
python -m pytest -q

# 6. 启动 LangGraph Server / Studio（第一个终端）
langgraph dev --host 127.0.0.1 --port 2024 --no-browser
# 浏览器打开 http://localhost:2024 即可在 Studio 中调用

# 7. 启动 FastAPI BFF（第二个终端）
uvicorn standard_document_assistant.api.app:app --host 0.0.0.0 --port 8080 --reload
# Health: http://127.0.0.1:8080/health
# Swagger: http://127.0.0.1:8080/docs
```

> Windows 下 `langgraph dev` 会自动加载 [agent.py](file:///d:/deep-agents/agent.py) /
> [metadata\_extraction\_graph.py](file:///d:/deep-agents/metadata_extraction_graph.py) /
> [standard\_review\_graph.py](file:///d:/deep-agents/standard_review_graph.py) 三个
> graph 入口（[langgraph.json](file:///d:/deep-agents/langgraph.json)）。
>
> 通过 FastAPI 接口执行标准文档审核的完整步骤见
> [design\_docs/FASTAPI\_BFF\_PHASE1\_API.md](file:///d:/deep-agents/design_docs/FASTAPI_BFF_PHASE1_API.md)，核心流程是：
> 交互式审核：创建 thread → 上传 PDF/DOCX/Markdown →
> `POST /api/threads/{thread_id}/standard-review/stream` → 如需审批则调用
> `POST /api/threads/{thread_id}/runs/resume` → 查询并下载产物。
> 生成工作流循环审核：上传生成稿 → 调用
> `POST /api/review-jobs/standard-review` 并传 `review_options` → 根据返回的
> `passed`、`review_report_markdown`、`review_result` 决定是否修改后再审。

***

## 16. 验证清单（运维/合规参考）

- [x] **三图注册**：`agent` / `metadata_extraction` / `standard_review` 全部可被 `langgraph dev` 加载
- [x] **双文件输入**：md + 配对 docx / pdf
- [x] **双轨审核**：内容（LLM）+ 格式（确定性）
- [x] **13-scope 切分**：cover / toc / foreword / ... / end
- [x] **规则提取**：FAISS 三件套 + TF-IDF JSON 双引擎，离线可降级
- [x] **多策略 LLM**：single / window / cross\_section / full\_document
- [x] **质量门控 + 回环**：`Command[Literal[...]]` + `max_review_rounds`
- [x] **报告产物**：report / result / trace / manifest 四件套
- [x] **scope\_summary 聚合**：按 `(audit_track, scope)` 桶
- [x] **LLM audit\_summary**：执行摘要 + 离线 fallback
- [ ] **Send 并行**：judge\_rules 异步并发 + Semaphore
- [ ] **Stream 命名空间**：`<domain>.<stage>` 统一
- [x] **Trace 注入**：parent callbacks 透传到子图
- [x] **HITL**：9 工具受控
- [x] **虚拟路径**：所有 IO 限定 `/workspace/`
- [x] **离线可跑**：无 API key / 无 FAISS 仍能走通

***

## 17. 长期记忆种子 — `memories/` 三份 `.md`

[`d:\deep-agents\memories/`](file:///d:/deep-agents/memories) 下的三份 `.md` 是**长期记忆的冷启动种子**，不是运行时数据存储；运行时的 `/memories/` 路径由 [`StoreBackend`](file:///d:/deep-agents/src/standard_document_assistant/agent.py#L96-L150) 路由到 LangGraph Store。详见[Memory](https://docs.langchain.com/oss/python/deepagents/memory)

### 17.1 文件角色与定位

| 文件                                                                              | 类别   | 性质               |
| ------------------------------------------------------------------------------- | ---- | ---------------- |
| [`memories/AGENTS.md`](file:///d:/deep-agents/memories/AGENTS.md)               | 长期规则 | **硬约束**（必须 / 不得） |
| [`memories/preferences.md`](file:///d:/deep-agents/memories/preferences.md)     | 用户偏好 | **软风格**（默认 / 引导） |
| [`memories/project-notes.md`](file:///d:/deep-agents/memories/project-notes.md) | 项目笔记 | **路径语义**说明       |

三者共同进 LangGraph Store 的 [`MEMORY_NAMESPACE`](file:///d:/deep-agents/src/standard_document_assistant/constants.py#L19-L25)（默认 `("standard-document-assistant", "memories")`），由 [`seed_memory_store()`](file:///d:/deep-agents/src/standard_document_assistant/agent.py#L71-L79) 在启动时灌入。

### 17.2 各自内容范围

#### [`AGENTS.md`](file:///d:/deep-agents/memories/AGENTS.md) — 长期规则（硬约束）

7 条带"必须 / 不得"的行为准则，违反任意一条视为越界：

- **语言**：标准文档相关输出默认使用中文
- **审核**：发现必须包含位置、问题、建议、依据；无法定位时写明"依据不足"
- **安全**：不保存整篇标准正文、密钥、个人隐私、未确认的模型推断
- **流程**：长期记忆更新必须通过 [`propose_memory_update`](file:///d:/deep-agents/src/standard_document_assistant/tools/__init__.py) 生成提案 + 人工审批
- **路径**：文件工具必须用虚拟路径（`/workspace/`、`/memories/`、`/skills/`），不得用 `D:\...` 或项目根目录
- **路径**：`/memories/` 是虚拟路径，不要尝试写本地 `memories/` 目录
- **范围**：当前阶段只实现最小可用智能体与关键节点测试

#### [`preferences.md`](file:///d:/deep-agents/memories/preferences.md) — 用户偏好（软风格）

3 条软引导，可被用户临时输入覆盖：

- 报告与草稿**默认 Markdown** 格式
- 风格**简洁**，但**关键风险和依据必须明确**（"简洁" ≠ "省略证据"）
- 产物**默认写入** `/workspace/output/`

#### [`project-notes.md`](file:///d:/deep-agents/memories/project-notes.md) — 项目笔记（路径语义）

4 条目录约定，与 [`build_permissions()`](file:///d:/deep-agents/src/standard_document_assistant/agent.py#L155-L189) 的 deny/allow 规则一一对应：

| 虚拟路径                    | 用途               | 读写属性   |
| ----------------------- | ---------------- | ------ |
| `/workspace/input/`     | 用户原始文档副本         | **只读** |
| `/workspace/output/`    | 审核报告、草稿、格式转换结果   | 写      |
| `/workspace/tmp/`       | 中间解析结果、测试临时文件    | 写      |
| `/workspace/templates/` | 报告模板、标准模板、本地参考材料 | **只读** |

### 17.3 注入方式

[`build_standard_document_agent`](file:///d:/deep-agents/src/standard_document_assistant/agent.py#L324-L412) 中：

- **主 Agent** **`memory=`**：[`/memories/AGENTS.md`、`/memories/preferences.md`](file:///d:/deep-agents/src/standard_document_assistant/agent.py#L381-L382)，由 Deep Agents 自动注入到 system prompt
- **`project-notes.md`** 未显式注入主 Agent `memory=` 列表，但与 `build_permissions` 的 deny/allow 规则一一对应，Agent 通过工具调用失败反馈感知
- **Store 灌入**：[`seed_memory_store(store)`](file:///d:/deep-agents/src/standard_document_assistant/agent.py#L71-L79) 在 `langgraph_server=False` 分支自动执行
- **`langgraph dev`（`langgraph_server=True`）**：因 [`store=None`](file:///d:/deep-agents/src/standard_document_assistant/agent.py#L361-L366)，**种子不会自动加载**；生产建议补一个 `bootstrap_dev_seed()` 在启动时通过平台 SDK 灌入

### 17.4 磁盘种子 vs 运行时虚拟路径

| 维度    | 磁盘 `memories/`    | 运行时 `/memories/`                               |
| ----- | ----------------- | ---------------------------------------------- |
| 本质    | 仓库源文件（git 版本化）    | `StoreBackend` 虚拟路径                            |
| 写入    | 人工编辑 + git commit | 走 `propose_memory_update` + HITL               |
| 持久化   | 仓库                | LangGraph Store（InMemory / Postgres）           |
| 多用户隔离 | 不隔离               | `(assistant_id, user_id)` 隔离                   |
| 跨线程共享 | N/A               | 同 namespace 共享                                 |
| 失败行为  | 文件缺失则 seed 跳过     | Store 不可用可由 `STANDARD_DOC_DISABLE_MEMORY=1` 旁路 |

### 17.5 关键代码入口

| 关注点               | 位置                                                                                              |
| ----------------- | ----------------------------------------------------------------------------------------------- |
| 种子读取              | [`agent.py:67-69`](file:///d:/deep-agents/src/standard_document_assistant/agent.py#L67-L69)     |
| Store 灌入          | [`agent.py:71-79`](file:///d:/deep-agents/src/standard_document_assistant/agent.py#L71-L79)     |
| 启动调用              | [`agent.py:365`](file:///d:/deep-agents/src/standard_document_assistant/agent.py#L365-L365)     |
| 主 Agent memory 注入 | [`agent.py:381`](file:///d:/deep-agents/src/standard_document_assistant/agent.py#L381-L382)     |
| 路径只读约束            | [`agent.py:185-189`](file:///d:/deep-agents/src/standard_document_assistant/agent.py#L185-L189) |
| 写路径工具             | 第 9 节 `propose_memory_update`（HITL ✅）                                                           |

***

## 18. 后续工作建议（非必须，按优先级）

1. **持久化**：
   - `langgraph-checkpoint-postgres` 替代 `MemorySaver`（生产 checkpointer）
   - `langgraph-store-postgres` 替代 `InMemoryStore`（生产 Store）
   - 部署层可托管时直接传 `langgraph_server=True`，由平台注入
2. **Send 显式扇出**：当 `len(active_scope_keys) × top_k > 100`，将 `judge_rules` 改造为
   `Send("judge_scope", {scope, rules})`，进一步压低审核延迟
3. **真实 embedding**：当前 `embedding_provider=dashscope + text-embedding-v3`
   已在配置位预留，尚未在 `FaissVectorRetriever` 内调用；接入后把
   `TfidfVectorizer` 换成 `DashScopeEmbeddings` 即升级为稠密向量
4. **Ragas / LangSmith Evaluator**：在线评估 LLM Judge 准确率；与人工抽检配对
5. **HITL 细化**：当前 `allowed_decisions=["approve", "edit"]`；可引入
   `edit` 的 schema 约束（限定可编辑字段），降低误改风险
6. **多模态**：MinerU 已落图，下一步可对图 / 表 / 公式做基于 VLM 的内容审核
7. **Skill 热更新**：当前 `/skills/` 走 FilesystemBackend，重启即可生效；可
   增加 `SkillWatcher` 做热加载
8. **Memory 与** **`/memories/`** **长期记忆**：
   - **当前实现**：
     - 磁盘上 `d:\deep-agents\memories\`（`AGENTS.md` / `preferences.md` /
       `project-notes.md`）**只是冷启动种子**，不是运行时数据存储；运行时
       `/memories/` 是 StoreBackend 管理的虚拟路径，落在 LangGraph Store 里
       （本地 `InMemoryStore` / `langgraph dev` 由平台注入）
     - `seed_memory_store()` 在 `langgraph_server=False` 分支自动执行；
       `langgraph_server=True`（即 `langgraph dev` / 部署）下 `store=None`，
       **种子不会自动加载**，`/memories/AGENTS.md` 等会缺失
     - Agent 对 `/memories/**` **只读**（`build_permissions` 显式 deny write），
       所有更新走 `propose_memory_update` + HITL 决策（`allowed_decisions=[
       "approve","edit"]`），应用层把批准内容写回 Store
     - namespace 默认 `("standard-document-assistant", "memories")`；`langgraph
       server` 模式下按 `assistant_id` + `user_id` 隔离，天然支持多用户
   - **生产环境建议**：
     - **Store 后端**：用 `langgraph-store-postgres` 替换 `InMemoryStore`，
       并设置 Store 的 `index`（`embed`、`dims`），便于按 namespace 检索
     - **种子引导**：补一个 `bootstrap_dev_seed()`，在 `langgraph_server=True`
       启动时检测目标 namespace 为空则通过平台 SDK 灌入 `memories/` 种子，
       保证 dev / staging / prod 行为一致
     - **多用户隔离**：生产 namespace 强制使用 `(assistant_id, user_id, "memories")`，
       跨用户访问一律拒绝；User 维度配置在 `langgraph_server` runtime 注入
     - **写路径审计**：把 `propose_memory_update` 提案落地为可审计对象
       （proposal\_id / proposer / diff / approver / timestamp），落到独立
       `audit_log` 表，不与 Store 混存
     - **可观测性**：暴露 `memories.list / memories.get / memories.propose`
       三类指标（QPS、命中率、拒绝率），配合 LangSmith Tracing 跟踪
       Store read/write 路径
     - **回滚策略**：每次写入生成版本号（Store item 的 `version` 字段），
       支持按版本回放；命名建议 `/memories/<file>@v<n>.md` 与现行 key
       形式兼容
     - **离线/降级**：保留 `STANDARD_DOC_DISABLE_MEMORY=1` 旁路开关，
       让标准审核主流程不依赖 Store 可用性，避免 PG 抖动影响审核 SLA

