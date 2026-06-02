# 标准文档助手（Deep Agents）

> 基于 **Deep Agents** + **LangGraph** 的国家级/行业级标准文档智能助手：解析 → 关键信息抽取 → 标准审核 → 报告生成 → 长期记忆。
> 框架严格遵循 Deep Agents 规范（虚拟文件路径、HITL、子代理、Skills、Memory 提案、LangSmith Trace），并落地了"FAISS + LLM Judge 多策略 + 质量门控 + 范围扩大回环 + scope_summary + audit_summary"完整内容审核链路。

---

## 1. 整体开发成果

### 1.1 工程定位
- **目标场景**：标准文档的检索、生成、审核、格式转化、关键信息抽取。
- **技术栈**：
  - Deep Agents（`create_deep_agent` 风格的子代理 + middleware 编排）
  - LangGraph（`StateGraph` 状态机子图 + `Command[Literal[...]]` 条件边 + 多轮回环）
  - LangChain（`ChatOpenAI` 兼容 DashScope Qwen / `FakeListChatModel` 离线回退 / `FAISS` 向量库）
  - LangSmith（可观测性 + trace + subagent 流式事件）
  - MinerU（`local` 自建 / `precise` 在线双模 PDF/DOCX 解析）

### 1.2 核心特性
| 维度 | 能力 |
|---|---|
| **多 Agent 编排** | 1 个主 Agent + 5 个 subagent（parser / extractor / reviewer / research / writer） |
| **Skills on-demand** | 4 个技能包：标准审核 / 信息抽取 / 文档生成 / 文档研究，按需加载 |
| **HITL** | 9 个高敏工具走 `interrupt_on`（写入、解析、抽取、审核、索引、记忆、执行等） |
| **虚拟文件** | 全部 IO 限定在 `/workspace/...`，`build_permissions` 强制落点 |
| **Memory** | 短期（State）+ 工作区（Filesystem）+ 长期（Store），记忆更新走提案制 |
| **Trace** | `invoke_traced_graph` 注入父级 callbacks；子图以 `standard_review` 节点呈现 |
| **子图** | `metadata_extraction`、`standard_review` 两个 LangGraph 子图，均带 `Command` 条件边 |
| **离线可跑** | FAISS 不可用时回退 TF-IDF；LLM 无 key 时回退 `FakeListChatModel` |

### 1.3 仓库目录

```
d:\deep-agents\
├── AGENTS.md                       # 主 Agent 工作约束
├── agent.py                        # LangGraph Server 入口（langgraph.json 加载点）
├── config.yaml                     # 应用/模型/MinerU/审核/记忆 配置
├── langgraph.json                  # LangGraph Server 部署配置
├── src/standard_document_assistant/
│   ├── config.py                  # 强类型配置 dataclass
│   ├── constants.py               # 路径/命名空间常量
│   ├── pathing.py                 # 虚拟路径 ↔ 真实路径 + Trace/产物 IO
│   ├── tracing.py                 # invoke_traced_graph / 节点名/工具名
│   ├── schemas.py                 # Pydantic 数据契约
│   ├── agent.py                   # 主 Agent + middleware + permissions + subagents
│   ├── artifacts.py               # 产物下载链接、注册器
│   ├── tools/
│   │   ├── parser.py              # parse_file_with_mineru / parse_document_with_mineru
│   │   ├── metadata.py            # extract_standard_metadata
│   │   ├── review.py              # 6 个审核工具（含 build_review_index）
│   │   └── validation.py          # validate_output_schema / propose_memory_update
│   ├── graphs/
│   │   ├── metadata_extraction/   # Langextract 元数据抽取子图
│   │   └── standard_review/       # 标准审核子图（本次重构重点）
│   │       ├── graph.py
│   │       ├── state.py
│   │       └── nodes/             # 6 个拆分节点：ingest/retrieve/review/aggregate/report/format_review
│   ├── review_core/               # 审核核心：parser/规则库/LLM Judge/上下文/序列化
│   │   ├── doc_parser.py
│   │   ├── word_parser.py
│   │   ├── pdf_format_parser.py
│   │   ├── format_audit.py
│   │   ├── knowledge_base.py      # FAISS 规则库
│   │   ├── retriever.py           # FAISS / TF-IDF 检索
│   │   ├── context_chunker.py     # 上下文裁剪
│   │   ├── llm_client.py          # ChatOpenAI 兼容 DashScope + 离线回退
│   │   ├── llm_judge.py           # 4 策略 LLM Judge（single/window/cross_section/full_document）
│   │   ├── audit_summary.py       # LLM 报告摘要 + 离线 fallback
│   │   ├── rule_models.py
│   │   ├── rules.py
│   │   ├── reporter.py
│   │   ├── serialization.py
│   │   └── scopes.py
│   ├── integrations/mineru/       # local / precise 双模 MinerU 客户端
│   └── resources/review_rules/    # rules_test.md + 自动构建的 rules.faiss.json
├── subagents/                      # 5 个子代理 AGENTS.md
│   ├── parser/AGENTS.md
│   ├── extractor/AGENTS.md
│   ├── reviewer/AGENTS.md
│   ├── research/AGENTS.md
│   └── writer/AGENTS.md
└── skills/                         # 4 个技能 SKILL.md
    ├── standard-review/SKILL.md
    ├── metadata-extraction/SKILL.md
    ├── document-generation/SKILL.md
    └── research/SKILL.md
```

---

## 2. 配置（`config.yaml` + 环境变量）

`config.yaml` 是单一事实源；`config.py` 把它解析为强类型 dataclass。
环境变量仅覆盖少量敏感字段（API Key、Base URL、模型名）。

### 2.1 `config.yaml` 顶层结构

| 段 | 关键字段 | 含义 |
|---|---|---|
| `app` | `name`, `default_language` | Agent 名 / 默认中文回复 |
| `models.primary` | `provider=qwen`, `model=qwen3.7-max`, `temperature=0` | 主对话模型 |
| `runtime` | `streaming=true`, `transport=sse`, `require_human_approval=true` | 流式 + 审批 |
| `workspace` | `uploads_dir`, `output_dir`, `allowed_upload_suffixes` | 虚拟目录与允许后缀 |
| `mineru` | `api_mode=local/precise`, `precise_poll_interval`, `request_options` | MinerU 解析 |
| `metadata_extraction` | `default_scope_mode`, `scoped_text_max_bytes`, `model.qwen3.5-flash`, `batch_length=40` | Langextract 抽取 |
| `standard_review` | `rules_md`, `index_dir`, `top_k=8`, `max_review_rounds=2`, `enable_llm_review=true` | 标准审核（本次扩充了 24 个字段，见 §6.5） |
| `memory` | `checkpointer=memory`, `store=inmemory`, `routes.{short_term,workspace,long_term}` | 记忆路由 |

### 2.2 关键环境变量

| 变量 | 用途 | 备注 |
|---|---|---|
| `DASHSCOPE_API_KEY` | LLM Judge / Metadata 抽取 / Embedding | Qwen 兼容 OpenAI 模式 |
| `DASHSCOPE_BASE_URL` | 覆盖 `https://dashscope.aliyuncs.com/compatible-mode/v1` | 可选 |
| `DASHSCOPE_JUDGE_MODEL` | 覆盖 `standard_review.judge_model` | 默认 `qwen3.5-flash` |
| `DASHSCOPE_EMBEDDING_MODEL` | 覆盖 `embedding_model` | 默认 `text-embedding-v3` |
| `LANGSMITH_API_KEY` | Trace 上报 | 可选，但生产强烈建议 |
| `LANGSMITH_PROJECT` | LangSmith 项目名 | 可选 |
| `MINERU_API_KEY` | precise 模式下需要 | local 模式可空 |
| `STANDARD_REVIEW_OVERRIDE` | JSON 覆盖 `standard_review` 段 | 紧急调参用 |

> **绝不**把以上任何 Key 写入 `config.yaml` 或提交到 Git。读取时全部走 `os.getenv`。

---

## 3. 子智能体（5 个）— `subagents/`

主 Agent 通过 `task(...)` 委派，每个 subagent 都有独立的 `system_prompt` + 工具白名单 + 可选 `interrupt_on` + `skills`。

| 名称 | 主要工具 | 何时调用 | 关键约束 |
|---|---|---|---|
| **parser** | `parse_file_with_mineru` / `parse_document_with_mineru` | 用户上传 PDF/DOCX，缺 Markdown/manifest 时 | 强制 HITL；先返回 `virtual_md_path` + `cover_metadata` |
| **extractor** | `extract_standard_metadata` | 输入已是 Markdown / MinerU 路径时 | 不得先读取全文；不得调用 `write_todos`；无需 SKILL 加载 |
| **reviewer** | 6 个审核工具（含 `build_review_index`、`inspect_review_rules`） | 进入审核流程 | HITL 覆盖解析/审核/索引；返回 4 路径 + scope_summary + audit_summary |
| **research** | 检索类 | 文档生成前的参考检索 | 仅在 writer 委派时使用 |
| **writer** | 生成类 | 草稿/报告/纪要起草 | 写入需 HITL；产物路径使用 `/workspace/output/...` |

> 详细见 `subagents/<name>/AGENTS.md`。所有子代理都通过 `build_subagents(use_hitl=True/False, langgraph_server=...)` 在 [agent.py](file:///d:/deep-agents/src/standard_document_assistant/agent.py#L253-L322) 集中注册。

---

## 4. Skills（4 个）— `skills/`

Deep Agents `SkillsMiddleware` 启动时扫描 `skills/` 目录，仅在 `name` 描述命中时按需加载；本项目 4 个技能全部遵循 SKILL.md frontmatter 规范。

| 技能 | 触发场景 | 关键内容 |
|---|---|---|
| [`standard-review/SKILL.md`](file:///d:/deep-agents/skills/standard-review/SKILL.md) | 用户请求"审核/审查/合规检查" | 子图拓扑、5 步默认工作流、工具表、产物路径、索引与 trace 说明 |
| [`metadata-extraction/SKILL.md`](file:///d:/deep-agents/skills/metadata-extraction/SKILL.md) | 抽取标准号/ICS/CCS/层级等元数据 | 何时调 parser、何时直接 extractor、产物 schema 校验 |
| [`document-generation/SKILL.md`](file:///d:/deep-agents/skills/document-generation/SKILL.md) | 生成标准/纪要/总结 | 澄清需求 → 检索 → 结构 → 草稿 → 自检 |
| [`research/SKILL.md`](file:///d:/deep-agents/skills/research/SKILL.md) | 在线检索参考标准 | 检索策略与高敏写操作 HITL |

每个 SKILL.md 都遵循统一 frontmatter：

```yaml
---
name: <skill-name>
description: 触发条件 + 能力概述 + 边界（满足 Deep Agents SkillsMiddleware 按需加载的描述要求）
---
```

---

## 5. 工具集（Tools）

`tools/__init__.py` 统一导出，分为 4 组：

### 5.1 Parser 工具

- `parse_file_with_mineru(file_path, ...)` — 真实路径入口；先做路径与大小检查，再调 MinerU 客户端。
- `parse_document_with_mineru(file_path, ...)` — Deep Agents 虚拟路径入口；解析后把 manifest + markdown 写入 `/workspace/input/uploads/...`，返回 `virtual_md_path` 与 `cover_metadata`。
- **MinerU 客户端** 见 [integrations/mineru/](file:///d:/deep-agents/src/standard_document_assistant/integrations/mineru/)：
  - `api_mode=local` → 走自建 `/file_parse`（pipeline 模式，解析方法 `auto`）。
  - `api_mode=precise` → 走 `mineru.net` 异步轮询（`vlm` 版本，间隔 3s，超时 600s）。
  - ZIP 解压到 `/workspace/output/mineru/<job_id>/`，图片保留到 `images/`，命名规则见 `naming.py`。

### 5.2 Metadata 工具

- `extract_standard_metadata(file_path | markdown, ...)` — 调 `metadata_extraction` 子图，产物 `<stem>_metadata.json` + manifest。

### 5.3 Review 工具（6 个，本次重构重点）

- `run_standard_review(content_path, source_path?, manifest_path?, target_scopes?, partial_mode?, force_rebuild_index?, ...)` — 端到端审核（FAISS + LLM Judge + 格式轨 + 报告）。
- `run_format_source_review(source_path, ...)` — 仅跑格式轨。
- `inspect_review_rules(query, scope?, top_k=5)` — FAISS 检索预览。
- `build_review_index(force_rebuild=True)` — 构建/重建 TF-IDF + FAISS 索引。
- `validate_review_result_schema(result_path)` — 校验产物 schema 与 `/workspace/` 前缀。

### 5.4 Validation / Memory 工具

- `validate_output_schema(payload, schema_name)` — 通用 schema 校验。
- `propose_memory_update(target, content)` — 长期记忆**提案**（不直接写 Store；HITL 批准后由应用层写入）。

> HITL 配置集中在 [agent.py:286-292](file:///d:/deep-agents/src/standard_document_assistant/agent.py#L286-L292)（subagent 级）和 [agent.py:336-347](file:///d:/deep-agents/src/standard_document_assistant/agent.py#L336-L347)（主 Agent 级）。

---

## 6. 标准审核模块（本次重构重点）

### 6.1 子图拓扑

```
START → ingest → retrieve_rules → judge_rules → quality_gate (Command[Literal[...]])
                                                       │
                          (insufficient_context & round < max_review_rounds)
                                                       ↓
                                          widen_review_scope → reload_review_rules
                                                       └──────→ judge_rules (回环)
                                                                              │
                                                                              (ok)
                                                                              ↓
                                                                         format_review
                                                                              ↓
                                                                          aggregate (scope_summary)
                                                                              ↓
                                                                  write_outputs (audit_summary)
                                                                              ↓
                                                                      write_manifest → END
```

- `quality_gate` 是 `Command[Literal["widen_review_scope", "format_review", "aggregate"]]`：单次 return 同时更新状态与跳转。
- `widen_review_scope` → `reload_review_rules` → `judge_rules` 形成**多轮回环**，最大 `max_review_rounds` 轮。
- 所有节点写 trace 事件到 `state["trace_events"]`（`Annotated[list, operator.add]` 累加）。

### 6.2 关键节点职责

| 节点 | 输入 | 关键产物 | 实现 |
|---|---|---|---|
| `ingest` | `content_path` / `source_path` / `manifest_path` | `parsed_document`, `scope_text_map`, `active_scope_keys` | 13-scope 切分（cover/toc/foreword/.../end），doc_parser + serialization |
| `retrieve_rules` | scope_keys + FAISS 索引 | `section_rules`, `full_document_rules`, `retrieval_trace` | knowledge_base + retriever |
| `judge_rules` | rules + 文档 + scope_text_map | `issues`, `insufficient_scopes`, `review_round` | LLMSoftRuleJudge 多策略异步 |
| `quality_gate` | 当前 issues + round | 跳转目标 | `Command[Literal[...]]` 条件路由 |
| `widen_review_scope` | 触发条件成立 | `partial_mode=full_document` | 重置 active_keys |
| `reload_review_rules` | 全文激活 keys | 重新 FAISS 检索 | 同 retrieve_rules |
| `format_review` | 源 PDF/DOCX | 确定性格式 issue | word_parser + pdf_format_parser + format_audit |
| `aggregate` | issues | `scope_summary` (按 (track, scope) 桶) | Counter 聚合 |
| `write_outputs` | 完整 state | 4 份产物 + `audit_summary` | reporter + audit_summary |
| `write_manifest` | 4 份产物 | `*_review_manifest.json` | ArtifactManifest |

### 6.3 双轨审核

| 轨 | 来源 | 触发器 | 工具 | 输出 |
|---|---|---|---|---|
| **内容轨（content_llm）** | MinerU Markdown | `enable_llm_review=true` | `LLMSoftRuleJudge` | `AuditIssue(status=fail/insufficient_context)` |
| **格式轨（format_source）** | 源 PDF/DOCX | 提供 `source_path` | `format_audit.run_format_source_audit` | `ReviewIssue(audit_track=format_source)` |

### 6.4 产物落盘

`/workspace/output/reviews/<job_id>/`（[pathing.review_output_root](file:///d:/deep-agents/src/standard_document_assistant/pathing.py)）：

| 文件 | 内容 | 用途 |
|---|---|---|
| `<stem>_audit_report.md` | 人类可读报告（Markdown） | 终端/前端展示 |
| `<stem>_audit_result.json` | 结构化 issues + scope_summary + audit_summary | 下游系统消费 |
| `<stem>_audit_trace.json` | 节点级 trace 事件 + 轮次/警告/路由 | 调试与审计 |
| `<stem>_review_manifest.json` | `ArtifactManifest` 4 路径 + 警告 | 跨系统溯源 |

### 6.5 `standard_review` 段配置（24+ 字段）

```yaml
standard_review:
  rules_md: src/standard_document_assistant/resources/review_rules/rules_test.md
  index_dir: src/standard_document_assistant/resources/review_rules
  top_k: 8
  max_review_rounds: 2
  enable_llm_review: true
  enable_audit_summary: true

  # LLM Judge
  judge_provider: dashscope-compatible
  judge_model: qwen3.5-flash          # env override: DASHSCOPE_JUDGE_MODEL
  judge_base_url: ""                  # env override: DASHSCOPE_BASE_URL
  judge_api_key_env: DASHSCOPE_API_KEY
  judge_temperature: 0.0
  judge_max_tokens: 2048
  judge_timeout: 60
  judge_max_retries: 2
  judge_max_workers: 4                # 异步并发 Semaphore

  # 嵌入 / 索引
  embedding_provider: dashscope
  embedding_model: text-embedding-v3
  embedding_dim: 1024
  auto_rebuild_index: true

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

  # 报告摘要
  summary_model: qwen3.5-flash
  summary_max_chars: 600
```

### 6.6 知识库与索引

- **规则源**：`src/standard_document_assistant/resources/review_rules/rules_test.md`（Markdown 章节即 RAG 段）。
- **解析器**：[knowledge_base._parse_rule_chunks](file:///d:/deep-agents/src/standard_document_assistant/review_core/knowledge_base.py) 按 H1/H2 切片，标注 `scope` / `analysis_mode`（`local` / `cross_section` / `full_document` / `deterministic`）/`target_scopes`。
- **索引**：`index_dir/rules.faiss.json`（序列化 VectorIndex）；`build_tfidf_index` 提供纯 Python fallback，FAISS 不可用时自动降级。
- **检索**：[retriever.search_faiss_or_tfidf](file:///d:/deep-agents/src/standard_document_assistant/review_core/retriever.py) — 优先 FAISS，回退 TF-IDF；scope 过滤。

### 6.7 LLM Judge 多策略

[LLMSoftRuleJudge](file:///d:/deep-agents/src/standard_document_assistant/review_core/llm_judge.py)：

| 策略 | 触发条件 | 用法 |
|---|---|---|
| `single` | `analysis_mode=local` 且 ctx 正常 | 1 rule + 1 context |
| `window` | ctx 超 `batch_window_max_chars` | 1 rule + 窗口（含跨节） |
| `cross_section` | `analysis_mode=cross_section` | 多节上下文 + 跨节规则 |
| `full_document` | `analysis_mode=full_document` | 全文 view + 全文规则 |

- 异步并发：`asyncio.gather` + `Semaphore(judge_max_workers)`。
- 流式进度：`get_stream_writer` 推送 `{"type": "scope_progress", "rule": chunk_id, "strategy": str}`。
- 置信度降级：`confidence < low_confidence_floor` → `status=insufficient_context`。
- 解析失败保护：`safe_json_loads` + `_empty_result` + `_fallback_issue`。

### 6.8 上下文构建

[DocumentContextBuilder](file:///d:/deep-agents/src/standard_document_assistant/review_core/context_chunker.py)：

- 按 scope 顺序构建 `_DocCache.ordered_units`（cover → toc → body → appendix → end）。
- 按 `target_scopes` 选取最相关 chunks，受 `local_context_max_chars` 截断。
- 提供 `structural_overview`（scope 长度分布）作为 LLM 输入。

### 6.9 报告摘要

[`generate_audit_summary`](file:///d:/deep-agents/src/standard_document_assistant/review_core/audit_summary.py)：

- 真实 LLM 模式：发 32 条以内 issues → JSON 4 字段（`summary` / `key_risks` / `top_fixes` / `insufficient`）。
- 离线回退：按 severity / scope 维度自动汇总，生成中英文混排的兜底摘要。

### 6.10 trace 与可观测

- 子图在 LangGraph Server / `langgraph dev` 中以 `standard_review` 节点呈现。
- `invoke_traced_graph` 把 parent callbacks / tags / metadata 注入 `RunnableConfig`；trace 包含 `round`、`widened`、`partial_mode`、`insufficient_scopes` 等元数据。
- 端到端 smoke 脚本 [scripts/final_smoke.py](file:///d:/deep-agents/scripts/final_smoke.py) 验证：build_index → inspect → run_standard_review → validate_review_result_schema 全部通过。

---

## 7. MinerU 模块

| 模式 | 端点 | 适用 | 关键配置 |
|---|---|---|---|
| `local` | 自建 `/file_parse`（pipeline 模式） | 内网/隐私场景 | `request_options.parse_method=auto`，`formula_enable/table_enable=true` |
| `precise` | `mineru.net` 异步精准解析 | 公网高质量需求 | `precise_poll_interval=3`，`precise_poll_timeout=600`，`vlm` 模型 |

调用流程（[client.py](file:///d:/deep-agents/src/standard_document_assistant/integrations/mineru/client.py)）：

```
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

---

## 8. Langextract 元数据抽取模块

`graphs/metadata_extraction/`：

| 文件 | 职责 |
|---|---|
| `state.py` | 抽取状态：源路径、Markdown、scope_mode、warnings |
| `graph.py` | `StateGraph`：解析 → 切片 → 提取字段 → 校验 → 产物 |
| `prompts.py` | 系统提示 + few-shot 模板 |
| `nodes.py` | 切片节点（按 scope 截取首段/封面） + 字段抽取节点 + 后处理 |
| `langextract_runner.py` | 包装 `langextract` 库：example_id、extractions、字符预算 |

`config.metadata_extraction.model` 默认 `qwen3.5-flash`；并行度 `max_workers=20`，`batch_length=40`。

抽出字段示例：`标准号 / 标准中文名称 / ICS / CCS / 标准层级 / 标准性质 / 发布日期 / 实施日期 / 起草单位 / 起草人` —— 见 `tools/metadata.py._SUMMARY_KEYS`。

---

## 9. 主 Agent 与 middleware 整合

[build_standard_document_agent](file:///d:/deep-agents/src/standard_document_assistant/agent.py#L323-L395) 通过 `create_deep_agent` 风格组装：

```python
agent = build_standard_document_agent(strict_model=True)
# 等价于:
# create_deep_agent(
#     model=qwen3.7-max,
#     tools=[...],
#     subagents=[parser, extractor, reviewer, research, writer],
#     skills=[standard-review, metadata-extraction, document-generation, research],
#     backend=FilesystemBackend(workspace, virtual_mode=True),
#     permissions=build_permissions(),
#     interrupt_on={...9 工具...},
#     checkpointer=MemorySaver(),
#     store=InMemoryStore(),
# )
```

`build_permissions()` 限定：

- 只读：`/skills/`, `/workspace/input/`, `/workspace/output/...`（产物读回）
- 写：`/workspace/output/`（必须走 HITL）
- 禁止：`.env`、密钥、项目根目录

---

## 10. Trace & LangSmith

- 所有子图入口走 `invoke_traced_graph` / `ainvoke_traced_graph`，自动透传 `callbacks` + `tags` + `metadata`。
- 节点级 trace 事件：每节点写一条 `{trace_id, job_id, component, node, event, status, created_at, ...}`。
- 推荐 `LANGSMITH_PROJECT=standard-document-assistant`，可在 Studio 直接看到：
  - 主 Agent ↔ subagent 委派链
  - `standard_review` 子图内 `ingest → retrieve → judge → quality_gate → ...` 拓扑
  - 工具调用参数与返回（含产物路径）

---

## 11. HITL（Human-in-the-Loop）

| 工具 | 触发原因 | 触发位置 |
|---|---|---|
| `write_file` / `edit_file` | 写产物 | 主 Agent |
| `execute` | 执行命令 | 主 Agent |
| `parse_file_with_mineru` / `parse_document_with_mineru` | 调用 MinerU 解析 | 主 Agent + parser subagent |
| `extract_standard_metadata` | LLM 抽取 | 主 Agent + extractor subagent |
| `run_standard_review` / `run_format_source_review` | 审核 | 主 Agent + reviewer subagent |
| `build_review_index` | 重建 FAISS | 主 Agent + reviewer subagent |
| `propose_memory_update` | 长期记忆 | 主 Agent |

所有 HITL 触发后通过 `Command(resume={"decisions": [{"type": "approve" | "reject" | "edit", ...}]})` 恢复。

---

## 12. 安装与运行

```bash
# 1. 安装依赖（建议 Python 3.10+）
pip install -r requirements.txt
# 关键包：deepagents, langgraph>=1.0, langchain>=1.0, langchain-openai,
#         langchain-community, faiss-cpu, python-docx, lxml, pymupdf, dashscope

# 2. 配置环境变量（写入 .env，gitignore）
DASHSCOPE_API_KEY=...
MINERU_API_KEY=...
LANGSMITH_API_KEY=...

# 3. 构建审核规则索引（首次 / 改 rules_test.md 后）
python -c "from standard_document_assistant.tools.review import _build_review_index_sync; print(_build_review_index_sync(force_rebuild=True))"

# 4. 端到端 smoke test
python scripts/final_smoke.py

# 5. 启动 LangGraph Server
langgraph dev
# 浏览器打开 http://localhost:2024 即可在 Studio 中调用
```

> Windows 下 `langgraph dev` 会自动加载 [agent.py](file:///d:/deep-agents/agent.py) 作为 `agent` 导出。

---

## 13. 验证清单（运维/合规参考）

- [x] **双文件输入**：md + 配对 docx/pdf
- [x] **双轨审核**：内容（LLM）+ 格式（确定性）
- [x] **13-scope 切分**：cover/toc/foreword/.../end
- [x] **规则提取**：FAISS + TF-IDF 双引擎，离线可降级
- [x] **多策略 LLM**：single/window/cross_section/full_document
- [x] **质量门控 + 回环**：`Command[Literal[...]]` + `max_review_rounds`
- [x] **报告产物**：report/result/trace/manifest 四件套
- [x] **scope_summary 聚合**：按 `(audit_track, scope)` 桶
- [x] **LLM audit_summary**：执行摘要 + 离线 fallback
- [x] **Trace 注入**：parent callbacks 透传到子图
- [x] **HITL**：9 工具受控
- [x] **虚拟路径**：所有 IO 限定 `/workspace/`
- [x] **离线可跑**：无 API key / 无 FAISS 仍能走通

---

## 14. 与参考 Skill 的差异说明

> 参考实现：[`D:\Chinese_national_standards_docs_Review-SKILL`](file:///D:/Chinese_national_standards_docs_Review-SKILL) 是一份独立的 LangGraph FastAPI 服务。

| 维度 | 参考 Skill | 当前 Deep Agents 实现 | 适配原因 |
|---|---|---|---|
| 框架 | LangGraph + FastAPI | Deep Agents + LangGraph 子图 | 用户要求 Deep Agents |
| 路径 | 真实文件系统 | 强制 `/workspace/` 虚拟路径 | Deep Agents 文件中间件 |
| 启动入口 | `run_audit.py` CLI | LangGraph Server `agent` 导出 | 适配 `langgraph dev` |
| 记忆 | 无 | 短期 + 工作区 + 长期 Store | Deep Agents MemoryMiddleware |
| 审批 | 外部 FastAPI 拦截 | `interrupt_on` | Deep Agents HumanInTheLoopMiddleware |
| 多 Agent | 单图 | 1 主 + 5 subagent | Deep Agents SubAgentMiddleware |
| Skills | 无 | 4 个 SKILL.md | Deep Agents SkillsMiddleware |
| 知识库 | `data/rules/rules.faiss` | `src/.../resources/review_rules/` | 适配项目布局 |
| Trace | 节点级 `event()` | `invoke_traced_graph` 透传 + 节点级事件 | 双层 trace |

**保留等价**：`format_audit.py` / `word_parser.py` / `pdf_format_parser.py` / `doc_parser.py` 几乎是直接迁移；`LLMSoftRuleJudge` / `DocumentContextBuilder` / `RuleKnowledgeBase` 完整重写以适配 Deep Agents 风格（async + `get_stream_writer` + Command 路由）。

---

## 15. 后续工作建议（非必须）

1. 接入真实 `langchain_community.vectorstores.FAISS` 的 `embeddings` 字段（当前回退为 `FakeEmbeddings`）。
2. 把 `_DEFAULT_RULES` 改为同步加载 `rules_test.md`（无文件时直接拒绝）。
3. 引入 `PostgresSaver` / `PostgresStore` 替代 `MemorySaver` / `InMemoryStore`（生产）。
4. 把 `quality_gate` 的回环可视化为 LangSmith 单独的 sub-run。
5. 引入 Ragas / LangSmith `Evaluator` 在线评估 LLM Judge 准确率。
