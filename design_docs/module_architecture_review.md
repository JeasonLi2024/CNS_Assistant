# 标准文档助手三模块 Deep Agents 架构适配性分析

> 适用代码：
> - MinerU 解析模块：[`src/standard_document_assistant/integrations/mineru/`](file:///d:/deep-agents/src/standard_document_assistant/integrations/mineru/) + [`src/standard_document_assistant/tools/parser.py`](file:///d:/deep-agents/src/standard_document_assistant/tools/parser.py) + [`skills/standard-parsing/SKILL.md`](file:///d:/deep-agents/skills/standard-parsing/SKILL.md)
> - langextract 元数据抽取模块：[`src/standard_document_assistant/graphs/metadata_extraction/`](file:///d:/deep-agents/src/standard_document_assistant/graphs/metadata_extraction/) + [`src/standard_document_assistant/tools/metadata.py`](file:///d:/deep-agents/src/standard_document_assistant/tools/metadata.py) + [`skills/standard-extraction/SKILL.md`](file:///d:/deep-agents/skills/standard-extraction/SKILL.md)
> - 标准审核模块：[`src/standard_document_assistant/graphs/standard_review/`](file:///d:/deep-agents/src/standard_document_assistant/graphs/standard_review/) + [`src/standard_document_assistant/review_core/`](file:///d:/deep-agents/src/standard_document_assistant/review_core/) + [`src/standard_document_assistant/tools/review.py`](file:///d:/deep-agents/src/standard_document_assistant/tools/review.py) + [`skills/standard-review/SKILL.md`](file:///d:/deep-agents/skills/standard-review/SKILL.md)
>
> 对照基准：Deep Agents v0.6 核心 Skill（`deepagents-core` / `deepagents-orchestration` / `deepagents-memory`）+ LangGraph 基础（`langgraph-fundamentals`）+ docs-langchain MCP（`customization` / `context-engineering` / `backends` / `store` / `subagents`）。
>
> 分析方式：按"现状 → 与最佳实践差距 → 影响 → 建议"四段式逐项展开；末尾给出综合优化路线。

---

## 1. 综合结论

| 模块 | 架构契合度 | 主要问题 | 优先级 |
| --- | --- | --- | --- |
| MinerU 解析 | ★★★★☆ | 工具同步阻塞；缺流式 / 进度；subagent `skills` 字段缺失；缺 `RetryPolicy`；HITL 覆盖宽泛 | 中 |
| langextract 元数据抽取 | ★★★★☆ | 子图无 checkpointer 但仍想用 HITL 决策 subagent；缺 `get_stream_writer`；断点续传依赖主图；缺 `Send` 并行切 scope；建议 `retry_policy` 未透传 | 中 |
| 标准审核 | ★★★★★（最契合） | 仍可下沉到 `langextract_runner` 的 `get_stream_writer` 推送；建议切到 `langgraph-prebuilt` ToolNode + 状态机自治；deepagents-store 抽象未复用；缺 `context_schema` | 低 |

> **整体判断**：三个模块都已采用"工具 + 子图 + Skills + HITL"四件套，结构上符合 Deep Agents 范式；尚未完全发挥的有：
> 1. 节点级流式进度（`get_stream_writer`）仅在 `standard_review` 部分节点有，未形成统一总线；
> 2. `RetryPolicy` / `handle_tool_errors` 缺失，长任务需要靠应用层 try/except + 离线 fallback；
> 3. `Runtime context`（`context_schema`）只在文档中提及，代码中未真正使用；
> 4. `Store` 长记忆 / `Backend` 类型与"工具虚拟路径"未完全对齐，工具代码自行处理 host / virtual 转换；
> 5. 错误恢复策略（节点级 vs 应用层）需统一。

---

## 2. 通用最佳实践基线

来自 `deep-agents-core` / `deep-agents-orchestration` / `deepagents-memory` Skill：

1. **Tools**：用 `StructuredTool.from_function` / `@tool`；复杂入参用 `ToolRuntime` + `InjectedToolArg` 注入配置/上下文，**业务字段不进 prompt**。
2. **Subagent**：显式 `name / description / system_prompt / tools / skills`；`skills` **不继承**主 agent。
3. **State**：节点 return `dict`（部分更新）；list 字段必须 `Annotated[list, operator.add]`；context 用 `Runtime[Context]`。
4. **Interrupt**：`interrupt_on` 配 `checkpointer=MemorySaver`（或 PostgresSaver）；HITL 决策用 `Command(resume={"decisions": [...]})`。
5. **Backend**：
   - 短期 scratch → `StateBackend`（默认）
   - 跨 session 长期记忆 → `StoreBackend`
   - 本地真实文件 → `FilesystemBackend(root_dir=..., virtual_mode=True)`
   - 混合 → `CompositeBackend`（最长前缀匹配）
6. **Skills**：`SKILL.md` 顶部 YAML frontmatter（`name` + `description`），按需加载；目录内可有 `references/` 配套资料。
7. **Memory**：默认只读 `/memories/AGENTS.md` + `preferences.md`；更新走 `propose_memory_update` + HITL，**严禁直写**。
8. **Error handling**：
   - transient → `RetryPolicy(max_attempts=3, initial_interval=1.0)`
   - LLM-recoverable → `ToolNode(tools, handle_tool_errors=True)` 把错误作为 ToolMessage 返回
   - user-fixable → `interrupt({"message": ...})`
   - unexpected → 抛出，由顶层 try/except + breadcrumbs 捕获
9. **Streaming**：节点内用 `from langgraph.config import get_stream_writer` 推送自定义数据；前端用 `stream_mode=["custom", "updates"]` 消费；推荐升级到 v0.6 typed-projection API（`agent.stream_events(version="v3")`）。

---

## 3. MinerU 解析模块

### 3.1 现状

- HTTP 客户端：[`integrations/mineru/client.py`](file:///d:/deep-agents/src/standard_document_assistant/integrations/mineru/client.py) 支持 `local`（自建 `/file_parse`）和 `precise`（云端四步：申请 URL → PUT 上传 → 轮询 → 下载 ZIP）两种模式。
- 工具封装：[`tools/parser.py`](file:///d:/deep-agents/src/standard_document_assistant/tools/parser.py) 用 `StructuredTool.from_function(func=, coroutine=)` 暴露 `parse_file_with_mineru`（sync 底层 + `asyncio.to_thread` async 包装）。
- ZIP 解析：[`integrations/mineru/zip_parser.py`](file:///d:/deep-agents/src/standard_document_assistant/integrations/mineru/zip_parser.py) 处理 `_middle.json` / `layout.json` / `content_list.json` 与图片命名。
- Skill：[`skills/standard-parsing/SKILL.md`](file:///d:/deep-agents/skills/standard-parsing/SKILL.md) 含 frontmatter（`name` / `description`）、调用模式表、`cover_metadata` 字段表、失败处理引用文件 `references/mineru-failures.md`。
- Subagent：`parser` 在 [`agent.py:build_subagents`](file:///d:/deep-agents/src/standard_document_assistant/agent.py#L170-L186) 注册，`tools=[parse_file_with_mineru]`，`skills=[parsing_skill]`；HITL `interrupt_on={"parse_file_with_mineru": True}`。

### 3.2 与最佳实践的差距

| 维度 | 现状 | 最佳实践 | 影响 |
| --- | --- | --- | --- |
| **工具同步/异步** | 同步 `_parse_file_with_mineru_sync`，主 Agent 调用时阻塞长请求（MinuerU precise 可能 10 分钟） | 长任务应 `coroutine=` 异步函数 + 主 Agent `ainvoke`；同步函数前用 `RetryPolicy` 包裹 | 阻塞主对话、影响多用户并发体验 |
| **`RetryPolicy`** | 无；用 `requests.raise_for_status` 后由应用层 try/except 捕获 `RuntimeError` | `add_node(name, func, retry_policy=RetryPolicy(max_attempts=3, initial_interval=2.0))` | 429 / 5xx 必须靠外层重试 |
| **`handle_tool_errors`** | 无；错误以 `RuntimeError` 抛出，被应用层 catch | `ToolNode(tools, handle_tool_errors=True)` 把错误作为 ToolMessage 返回，让 LLM 自决 | 主 Agent 看到的是 raw 错误信息，无法继续规划 |
| **流式进度** | 工具 `return` 最终 manifest dict；`precise` 模式轮询时无任何信号给前端 | 节点 / 工具内 `get_stream_writer()` 推 `progress` / `phase` | 大文件时前端只看到 "spinner"，无阶段提示 |
| **断点续跑** | 已有 `skip_if_zip_exists`（基于本地 ZIP 是否存在） | 与 checkpointer 协同：HITL 拒绝 / 网络中断时只重放最后阶段 | 现状基本可用，但若 MinerU partial 产物（middle.json 已下载、ZIP 未下载）无法续传 |
| **HITL 粒度** | 整工具 `interrupt_on={"parse_file_with_mineru": True}` | 可结合 `interrupt_on={"parse_file_with_mineru": {"allowed_decisions": ["approve", "edit"]}}` 防止误 reject | 暂无 `edit` 决策能力 |
| **`tools` 类型** | `StructuredTool.from_function` 已正确；`ToolRuntime` 注入 `runtime: ToolRuntime \| None, InjectedToolArg` | 一致 ✓ | OK |
| **路径解析** | `resolve_workspace_read_path` 在工具内自做 `ensure_within` | 与 `FilesystemBackend(virtual_mode=True)` 行为对齐 ✓ | OK；但意味着 `/workspace/` 由工具层自己把守，没有走 Deep Agents 自带 `read_file` |
| **Backend 选择** | 工具内直接落 `OUTPUT_DIR / "mineru"` | 建议归 `FilesystemBackend`（用户已在 `agent.py:build_backend` 中开启 `/workspace/`） | OK；当前路径前缀一致；后续切换 S3/OSS 时要换实现 |
| **Subagent skills** | `parser_spec["skills"] = [parsing_skill]` ✓ | 一致 | OK |
| **Frontmatter** | `name: standard-parsing` + `description: ...` ✓ | 一致 | OK；但 description 可以更具体（"PDF/Word → Markdown via MinerU"） |

### 3.3 优化建议

1. **拆分同步/异步**：把 `_parse_file_with_mineru_sync` 拆为同步实现 + 异步 wrapper（`asyncio.to_thread`），主 Agent 自动选择；`StructuredTool.from_function(..., coroutine=_async_version)`。
2. **`RetryPolicy` 注入**：在主图层面给 `parse_file_with_mineru` 包一层 `ToolNode(tools, handle_tool_errors=True, ...)`；对 `_request_local_parse_file` 应用 `RetryPolicy(max_attempts=3, initial_interval=2.0, max_interval=10.0, jitter=True)`。
3. **进度推送**：在 `precise` 轮询中加 `get_stream_writer({"type": "mineru.precise.poll", "state": state, "elapsed_s": ...})`；`local` 单次请求可推 `mineru.local.request` / `mineru.local.response`。
4. **HITL 决策粒度**：在 `agent.py:build_subagents` 给 `parser_spec["interrupt_on"]` 改为 `{"parse_file_with_mineru": {"allowed_decisions": ["approve", "edit"]}}`，保留 "reject" 给显式禁用的场景。
5. **断点续跑扩展**：把 `middle.json` / `layout.json` 等中间产物也落盘，下次调用按 `manifest_path` 探测，缺哪个补哪个。
6. **Skill description 精确化**：把"解析 PDF/Word 到 Markdown"加上前置条件（"仅在 `/workspace/input/uploads/**` 且后缀 .pdf/.docx"），减少误召。

---

## 4. langextract 元数据抽取模块

### 4.1 现状

- 子图：[`graphs/metadata_extraction/graph.py`](file:///d:/deep-agents/src/standard_document_assistant/graphs/metadata_extraction/graph.py) 7 节点线性：`load_markdown → slice_scope → run_langextract → aggregate_fields → validate_schema → persist_output → write_manifest`。
- State：[`graphs/metadata_extraction/state.py`](file:///d:/deep-agents/src/standard_document_assistant/graphs/metadata_extraction/state.py) 用 `TypedDict, total=False`；list 字段（`quality_warnings` / `errors` / `warnings`）用 `Annotated[list, operator.add]` ✓。
- 工具：[`tools/metadata.py`](file:///d:/deep-agents/src/standard_document_assistant/tools/metadata.py) 暴露 `extract_standard_metadata`，同步 + 异步双实现；调用 `get_metadata_extraction_graph()` 并通过 `invoke_traced_graph` 透传 parent callbacks / tags / metadata。
- Subagent：`extractor` 在 `agent.py:build_subagents` 注册，`tools=[extract_standard_metadata, validate_output_schema]`，`skills=[extraction_skill]`。
- Skill：[`skills/standard-extraction/SKILL.md`](file:///d:/deep-agents/skills/standard-extraction/SKILL.md) 有 frontmatter + 8 条 Instructions（明确禁止预读 Markdown 全文、禁止修改 JSON、禁止再读 skill）。
- Langextract runner：[`graphs/metadata_extraction/langextract_runner.py`](file:///d:/deep-agents/src/standard_document_assistant/graphs/metadata_extraction/langextract_runner.py) 在 `run_langextract` 节点中调用 `run_extraction(scoped_text)`，按 `batch_length` / `max_workers` / `extraction_passes` 控制并发。

### 4.2 与最佳实践的差距

| 维度 | 现状 | 最佳实践 | 影响 |
| --- | --- | --- | --- |
| **子图 checkpointer** | `get_metadata_extraction_graph().compile()` 不带 checkpointer | 短任务可不带；长任务或 HITL 必备 | 当前 OK；但若元数据抽取中途 HITL / 中断，无法跨重启恢复 |
| **子图重试** | `_traced_run_extraction` 内部 try/except，错误进 `errors` 列表 | 节点级 `retry_policy=RetryPolicy(max_attempts=2, initial_interval=1.0, retry_on=requests.exceptions.RequestException)` | 429 仍由应用层捕获 |
| **流式进度** | 节点内无 `get_stream_writer`；前端只能从 `plan.updated` 推 `extractor.subagent` 知道在做 | 节点内 `get_stream_writer` 推 `meta.scoped` / `meta.extraction.start` / `meta.extraction.end` | 前端看不到切了多少字符、抽了多少字段 |
| **`Send` 并行** | `run_langextract` 是单节点；`slice_scope` 一次性切完 | 未来可 `slice_scope` 拆 scope → `Send` fan-out → `aggregate_fields` 收 | 当前 Markdown 长度可控时可接受 |
| **Context schema** | 无 `context_schema`；节点签名 `def xxx(state) -> dict` | `def xxx(state, runtime: Runtime[Context]) -> dict`；context 含 `trace_id` / `user_id` | 当前用 `state` 字段传 `trace_id` / `cover_metadata_hint`，可工作但耦合 |
| **`handle_tool_errors`** | `extract_standard_metadata` 无；子图错误返回 `{status: "failed", errors: [...]}` | `ToolNode(tools, handle_tool_errors=True)` 接收 ToolMessage；或子图外层 try/except | 工具调用失败时主 Agent 只能看到 raw 错误 |
| **Schema 校验** | `validate_schema` 节点用 Pydantic `model_validate`，`strict_validation=False` 时保留原始聚合 | 与 `validate_output_schema` 工具重复 | 二者职责可以合并（节点内调用工具） |
| **产物注册** | `persist_output` 落盘后，`_attach_download_and_register` 调 `artifacts.register_from_tool_result` | ✓ 一致 | OK |
| **Subagent skills** | `extractor_spec["skills"] = [extraction_skill]` ✓ | 一致 | OK |
| **`description`** | Skill description 含 "16 standard metadata fields" | 具体可继续细化（"从 Markdown 国标抽取 GB/T 字段"） | OK |
| **`output_format`** | Pydantic `MetadataExtractionResult` + 工具返回 dict | `response_format=MetadataExtractionResult` 可在 `create_deep_agent` 顶层用 | 子图工具为主，`response_format` 在主代理侧无影响 |

### 4.3 优化建议

1. **节点内流式**：在 `slice_scope` / `run_langextract` / `aggregate_fields` 三个长任务节点加 `get_stream_writer`（参考 `stream_sse_design.md` §5.1），覆盖 `meta.scoped` / `meta.extraction.delta` / `meta.aggregated`。
2. **RetryPolicy 注入**：把 `run_langextract` 节点注册为 `add_node("run_langextract", run_langextract, retry_policy=RetryPolicy(max_attempts=2, initial_interval=1.0, retry_on=(requests.exceptions.RequestException, RuntimeError)))`。
3. **引入 context_schema**：定义 `MetadataExtractionContext(TypedDict)` 含 `tenant_id / user_id / trace_id / quality_strict`；编译时 `compile(context_schema=MetadataExtractionContext)`；节点签名 `def run_langextract(state, runtime: Runtime[MetadataExtractionContext]) -> dict`。
4. **Pydantic 校验去重**：在 `validate_schema` 节点内调用 `validate_output_schema` 工具（已存在），统一 schema 名称 / 错误格式。
5. **Send 并行化（未来）**：当 `scope_mode="full"` 且文本超阈值时，把 `run_langextract` 拆为 `slice_scope` → 多个 `run_langextract` worker（用 `Send`）→ `aggregate_fields`。
6. **HITL 跨子图**：若希望用户在 langextract 失败时人工补充字段，需要子图带 `checkpointer`；建议引入 `MemorySaver`/`SqliteSaver` 仅给 `metadata_extraction` 子图，命名空间与主图隔离。
7. **Skill description 调优**：突出"Markdown 输入"、"不预读全文"、"不直改 JSON"等关键约束，便于 `task` 委派时 LLM 正确判断。

---

## 5. 标准审核模块

### 5.1 现状

- 子图：[`graphs/standard_review/graph.py`](file:///d:/deep-agents/src/standard_document_assistant/graphs/standard_review/graph.py) 9 节点：`ingest → retrieve_rules → judge_rules → quality_gate (Command[Literal[...]]) → widen_review_scope → reload_review_rules → format_review → aggregate → write_outputs → write_manifest`。
- State：[`graphs/standard_review/state.py`](file:///d:/deep-agents/src/standard_document_assistant/graphs/standard_review/state.py) 完整：`issues / warnings / errors / events / trace_events` 均用 `Annotated[list, operator.add]` ✓；`review_round / widened / insufficient_scopes` 支持 widen 回环。
- LLM Judge：[`review_core/llm_judge.py`](file:///d:/deep-agents/src/standard_document_assistant/review_core/llm_judge.py) 多策略 `single / window / cross_section / full_document`，`asyncio.gather` + `Semaphore(judge_max_workers)` 控制并发，置信度降级，`insufficient_context` 状态。
- 质量门控：[`graphs/standard_review/nodes/review.py:quality_gate`](file:///d:/deep-agents/src/standard_document_assistant/graphs/standard_review/nodes/review.py#L101-L137) 用 `Command[Literal["widen_review_scope", "aggregate", "format_review"]]` 单次 return 同时更新 `trace_events` 与跳转。
- Widen 回环：`widen_review_scope → reload_review_rules → judge_rules` 形成多轮回环，由 `max_review_rounds` 终止；`widened=True` 标记、`review_round` 自增。
- 工具：[`tools/review.py`](file:///d:/deep-agents/src/standard_document_assistant/tools/review.py) 暴露 5 个：`run_standard_review` / `run_format_source_review` / `inspect_review_rules` / `build_review_index` / `validate_review_result_schema`；其中前 4 个在 subagent 与主 agent 两层都加了 HITL。
- Subagent：`reviewer` 在 `agent.py:build_subagents` 注册，6 个工具 + 1 skill；`interrupt_on` 覆盖 `parse_file_with_mineru` / `run_standard_review` / `run_format_source_review` / `build_review_index`（`allowed_decisions: ["approve", "edit"]`）。
- Skill：[`skills/standard-review/SKILL.md`](file:///d:/deep-agents/skills/standard-review/SKILL.md) 含子图拓扑图、双轨描述、Tool Set 表、Default Workflow、Artifact Layout、Knowledge Base 说明、Trace & Resumption 段落。
- Reviewer AGENTS.md：[`subagents/reviewer/AGENTS.md`](file:///d:/deep-agents/subagents/reviewer/AGENTS.md) 6 步工作流 + 强约束（双文件、不得覆盖原始、不得伪造来源、依据不足标记）。
- 产物 4 份：`report / result / trace / manifest`，全部 `/workspace/output/reviews/<job_id>/`；`scope_summary` 按 `(audit_track, scope)` 聚合；`audit_summary` LLM 报告摘要；`retrieval_trace` 含策略分布与命中规则数。

### 5.2 与最佳实践的差距

| 维度 | 现状 | 最佳实践 | 影响 |
| --- | --- | --- | --- |
| **`Command` 路由** | `quality_gate` 用 `Command[Literal[...]]` 单次 return ✓ | 一致 | OK；多轮回环天然适合 |
| **节点签名** | `def judge_rules(state) -> dict`；`def quality_gate(state) -> Command[...]` | 推荐 `def judge_rules(state, runtime: Runtime[Context]) -> dict`（用 context 取 trace_id） | 当前用 `state.get("trace_id")`；与 `runtime.context` 重复 |
| **`get_stream_writer`** | 在 `review.judge_summary` 写入 `state.events`，但**未**通过 stream_writer 推 | 节点内 `writer({"type": "review.judge.start", "round": ..., "total_plans": ...})` | 13 scope × 多策略 × 多轮的过程对前端不可见 |
| **`RetryPolicy`** | LLM Judge 内部 try/except，错误进 `errors` 列表 | 节点 `retry_policy=RetryPolicy(max_attempts=2, retry_on=requests.exceptions.RequestException)` | LLM 临时故障不会自动重试 |
| **子图 checkpointer** | `get_standard_review_graph().compile(name=STANDARD_REVIEW_GRAPH_NAME)` 不带 checkpointer | widen 回环跨重启需要 checkpointer | 现状回环在同一进程内可用；崩溃后无法续 |
| **HITL** | subagent 与主 agent 两层都加 `interrupt_on`（见 `agent.py:agent_kwargs["interrupt_on"]`） | 双层 interrupt 会出现"工具先 HITL，subagent 又 HITL"两次审批 | 体验冗余，建议二选一 |
| **Context schema** | 无 | `compile(context_schema=StandardReviewContext)` 含 `tenant_id / user_id / trace_id` | 当前通过 `state["trace_id"]` 透传 |
| **Frontend consistency** | `tools.json` 工具定义、AGENTS.md 6 步工作流、SKILL.md Tool Set 表，三者口径一致 ✓ | OK | 保持 |
| **LLM Judge 离线 fallback** | `_offline_judge` 在 `LLMSoftRuleJudge.run_dual_route` 内部 try/except + `FakeListChatModel` | ✓ 与 docs 一致 | OK |
| **Pydantic 校验** | 工具侧 `validate_review_result_schema` 工具，节点侧 `_to_audit_issue` 中英映射 | 节点侧校验后工具侧再校验，存在重复 | 保留 OK，节点侧确保主流程产物合规、工具侧是用户自检 |
| **确定性 fallback** | `format_review` 节点对 DOCX/PDF 做章编号 / 条层次 / 列项 / 目次页码检查 | ✓ 与 reference 实现一致 | OK |
| **范围扩大算法** | `widen_review_scope` 改 `partial_mode` 为 `full_document`，扩 9 个 scope | ✓ | OK |
| **Breadcrumbs** | `state.breadcrumbs` 暂无；`trace_events` 已有 | 建议新增 `breadcrumbs` 记录 HITL / widen / aggregate 完成 | 与 multi_user_runtime_design.md §8.2 对齐 |

### 5.3 优化建议

1. **节点统一 stream_writer**：在 `judge_rules` / `quality_gate` / `widen_review_scope` / `reload_review_rules` / `format_review` / `aggregate` 节点用 `ProgressBus` 推送（参见 `stream_sse_design.md` §5.2），形成 6 节点全链路进度可见。
2. **节点签名升级**：把 9 个节点的签名改为 `(state, runtime: Runtime[StandardReviewContext])`，`trace_id` / `review_round` 由 runtime 注入；保留 `state` 兼容（fallback 到 `state.get`）。
3. **节点级 RetryPolicy**：`judge_rules` 与 `aggregate` 加 `retry_policy=RetryPolicy(max_attempts=2, initial_interval=1.0, retry_on=(requests.exceptions.RequestException, RuntimeError))`。
4. **子图 checkpointer**：与主图共享 `MemorySaver`（本地）/ `PostgresSaver`（生产）；`compile(checkpointer=...)` 由 `build_standard_document_agent` 注入。
5. **HITL 二选一**：subagent 层与主 agent 层的 `interrupt_on` 合并，仅在"产生实际副作用"（写文件、调用外部服务）处触发，避免双层弹窗；用 `allowed_decisions=["approve","edit"]` 收紧语义。
6. **Breadcrumbs 落地**：新增 `BreadcrumbsMiddleware`（AgentMiddleware）或子图 wrapper 节点，收集 HITL 决策、widen 触发、aggregate 完成、产物注册等关键事件，写入 `state.breadcrumbs`，随 `write_manifest` 落盘。
7. **Pydantic 校验去重**：让 `validate_review_result_schema` 工具改为"读 manifest 路径校验"，节点内 `write_outputs` 写完后自动调一次，结果写回 `state.validation`。
8. **Tool set 与 SKILL.md 同步**：每次新工具或新参数，同步更新 [`subagents/reviewer/AGENTS.md`](file:///d:/deep-agents/subagents/reviewer/AGENTS.md) 与 [`skills/standard-review/SKILL.md`](file:///d:/deep-agents/skills/standard-review/SKILL.md) 的 Tool Set 表 + Default Workflow。

---

## 6. 跨模块通用优化

### 6.1 工具层

| 现状 | 建议 |
| --- | --- |
| 三个业务工具均用 `StructuredTool.from_function` + `ToolRuntime, InjectedToolArg` | ✓ 一致；建议补 `args_schema`（Pydantic model）做参数级校验 |
| 工具错误处理靠应用层 `try/except` | 把 transient / validation / internal 分类后映射到 `ToolNode(..., handle_tool_errors=True)` 返回的 ToolMessage；让 LLM 看到结构化错误后自决 |
| 工具同步实现主路径，异步实现走 `coroutine=` | 长任务（MinuerU / LLM Judge）应主推异步，主 Agent 走 `ainvoke` |

### 6.2 Subagent 层

| 现状 | 建议 |
| --- | --- |
| 5 个 subagent 全部显式 `skills` ✓ | OK |
| `parser / extractor / reviewer` 都在 subagent 层 + 主 agent 层同时配 `interrupt_on` | 合并为单层；用"是否写文件 / 是否调外部服务"判定 |
| `system_prompt` 中文 + 强约束 ✓ | 保持 |
| 没有 subagent 级别的 `state_schema` 扩展 | 未来若 subagent 需要"中间 scratch" 状态，可在 `task(...)` 上下文加 ephemeral state；当前不需要 |

### 6.3 State / Graph 层

| 现状 | 建议 |
| --- | --- |
| list 字段全部 `Annotated[list, operator.add]` ✓ | 保持 |
| `total=False` + 部分节点用 `state.get("xxx")` 容错 ✓ | 保持 |
| `quality_gate` 用 `Command[Literal[...]]` ✓ | 保持 |
| `aggregate → write_outputs → write_manifest` 线性；可考虑 `Send` 并行 | 当前单线性 OK；未来如加 multi-format 报告（markdown + html）可 `Send` |
| 节点内部 try/except 包了大多数错误 | 抽出 `safe_node(func)` 装饰器统一 `errors` / `warnings` 注入 + breadcrumb 写入 |

### 6.4 Backend / Memory

| 现状 | 建议 |
| --- | --- |
| `CompositeBackend` 路由 `/memories/`（StoreBackend）+ `/skills/`（FS）+ `/workspace/`（FS） | 部署时切到 `StoreBackend` 全部 + S3/OSS 适配 `FilesystemBackend`；namespace 工厂加 `kind` 维度（memories / agent_assets / review_cache / run_meta） |
| `_memory_namespace_factory` 已实现按 assistant_id + user 隔离 | 部署时启用；本地保留 `MEMORY_NAMESPACE` 兜底 |
| 长期记忆走 `propose_memory_update` + HITL 提案，不直写 | ✓ 保持 |
| `/memories/` 的写入权限 deny | ✓ 保持；Proposal 模式符合 `deepagents-memory` 建议 |

### 6.5 Skills 层

| 现状 | 建议 |
| --- | --- |
| 4 个 skill 都有 frontmatter ✓ | 保持 |
| `description` 比较精炼 | 加"何时 **不** 用"段，便于 `task` 委派时 LLM 不误召 |
| 部分 skill 有 `references/` 配套（如 `mineru-failures.md`、`metadata-fields.md`） | 其余 skill（review / drafting）也补充 `references/`：常见错误、最佳实践、字段对照 |
| 4 个 skill 通过 `build_subagents` 显式注入 subagent | 保持 |

### 6.6 Runtime / Context（与 `multi_user_runtime_design.md` 对齐）

| 现状 | 建议 |
| --- | --- |
| `RuntimeContext` 已设计未落地 | 落地为 `TypedDict`，主图与子图 `compile(context_schema=...)`；节点签名 `(state, runtime)` |
| `thread_id` 单一字符串 | 升级为 `ThreadId(tenant:user:session[:branch])`；`build_thread_config` 解析后只把 session 段写入 LangGraph |
| 多用户文件目录扁平 | 升级为 `workspace/output/<kind>/<tenant>/<user>/<session>/`；`resolve_workspace_read_path` 接受 `thread: ThreadId` 强制 `allowed_roots` |

### 6.7 流式 / SSE（与 `stream_sse_design.md` 对齐）

| 现状 | 建议 |
| --- | --- |
| `streaming.py` 用 `astream(stream_mode=["updates", "values"])` 手工分支 | 升级到 `astream_events(version="v3")` typed-projection |
| 节点内流式仅在 `standard_review.review.judge_summary` 隐式实现 | 引入 `streaming/progress.py:ProgressBus` 统一总线 |
| 无 heartbeat / `since` 重连 | 实现 `stream_agent_sse_v3`（详见 `stream_sse_design.md` §4） |

---

## 7. 实施路线（建议）

| 阶段 | 目标 | 关键改动 | 涉及模块 |
| --- | --- | --- | --- |
| **Phase 1**（~1 周） | 节点流式统一 | `streaming/progress.py` 总线 + 三个子图节点加 `get_stream_writer`；`streaming.py` 升 `v3` | MinerU / langextract / standard_review |
| **Phase 2**（~3 天） | 工具错误处理统一 | 工具级 `ToolNode(..., handle_tool_errors=True)` + 节点级 `RetryPolicy` | 三个工具 |
| **Phase 3**（~1 周） | RuntimeContext 落地 | 主图 + 两个子图 `context_schema`；`build_context(request)`；HITL 行为保持 | 三个子图 |
| **Phase 4**（~3 天） | Subagent HITL 单层化 | 合并 `agent_kwargs["interrupt_on"]` 与 `subagent_spec["interrupt_on"]`；用 `allowed_decisions` 收紧 | `agent.py:build_subagents` |
| **Phase 5**（~1 周） | 子图 checkpointer | `standard_review` 共享主图 checkpointer；`metadata_extraction` 视需要加 | 两个子图 |
| **Phase 6**（~1 周） | Breadcrumbs + Trace 统一 | `BreadcrumbsMiddleware` + `state.breadcrumbs` + 随 `write_manifest` 落盘 | 主图 + 两个子图 |
| **Phase 7**（~持续） | 持久化 / 多用户 | `PostgresSaver + PostgresStore` + pgvector + `ThreadId` 路径隔离；详见 `multi_user_runtime_design.md` | 全局 |

---

## 8. 验收清单（针对三模块）

### MinerU
- [ ] `parse_file_with_mineru` 提供 `coroutine=async_func` 异步版本
- [ ] `add_node` 包裹 `RetryPolicy(max_attempts=3, initial_interval=2.0)`
- [ ] `precise` 模式轮询中通过 `get_stream_writer` 推 `mineru.precise.poll`
- [ ] Skill `description` 明确前置条件
- [ ] `interrupt_on` 改 `{"allowed_decisions": ["approve","edit"]}`

### langextract
- [ ] `slice_scope` / `run_langextract` / `aggregate_fields` 通过 `ProgressBus` 推 `meta.*`
- [ ] `run_langextract` 节点配 `RetryPolicy`
- [ ] `compile(context_schema=MetadataExtractionContext)` + 节点签名 `(state, runtime)`
- [ ] `validate_schema` 复用 `validate_output_schema` 工具
- [ ] Skill `description` 强调"不预读全文 / 不直改 JSON"

### standard_review
- [ ] 9 节点全部通过 `ProgressBus` 推对应事件
- [ ] 9 节点签名升级 `(state, runtime)`，trace_id 从 runtime 注入
- [ ] `judge_rules` / `aggregate` 配 `RetryPolicy`
- [ ] `compile(checkpointer=...)` 共享主图持久化
- [ ] HITL 二选一（subagent 或主 agent，单层）
- [ ] `BreadcrumbsMiddleware` 写入 `state.breadcrumbs`
- [ ] `SKILL.md` Tool Set 表与 `subagents/reviewer/AGENTS.md` 同步

---

## 9. 未来可优化点（与 Deep Agents 路线对齐）

1. **Deep Agents v0.6 typed-projection 流式** 全面上线后，下线 `stream_mode="updates/values"` 兼容层。
2. **`response_format=MetadataExtractionResult | ReviewToolResult`**：主代理直接要求 LLM 输出结构化产物，与 Pydantic schema 绑定。
3. **`state_schema` 扩展**：把 `breadcrumbs / quality_warnings / insufficient_scopes` 等加入主图 `state_schema`，让跨 subagent 可见。
4. **Deep Agents 自带 `task` 工具与 subagent 中 `task(...)` 二次委派**：当前 subagent 不会再次委派；如未来需要"审核 subagent 内调用 parser subagent"，需要 `subagent_spec["subagents"]` 嵌套（Deep Agents v0.6 支持）。
5. **Skills 增加 `examples/` 与 `tests/`**：每 skill 配一个 `tests/test_skill.py`，验证描述可被 LLM 正确理解（类似 Anthropic 的 skill eval）。
6. **`StoreBackend` 跨 thread 知识共享**：把 `rules_test.md` 的 FAISS 索引 cache 存到 Store，namespace=`(assistant_id, "assets", "review_index")`，避免每个 thread 重建。
7. **`SandboxBackend`（如可用）**：未来 `/workspace/` 切到沙箱执行（gVisor / Firecracker），把 `execute` 工具隔离在沙箱。
8. **LangSmith Engine 接入**：自动检测 long-running / 失败节点，配合 docs-langchain "Customize Deep Agents" 中提到的 LangSmith Engine，自动建议修复（refine prompt / 切换模型 / 调整 max_workers）。
9. **Deep Agents 自带 summarization middleware**：当 thread 内 `messages` 超过阈值时自动摘要，与我们的 `audit_summary` 形成两级摘要（局部 scope_summary + 全局 audit_summary）。
10. **Managed Deep Agents 适配**：参考 docs-langchain `managed-deep-agents-deploy`，把项目适配为 AGENTS.md + skills/ + subagents/ + tools.json 的"四件套"形态，托管到 LangSmith。

---

## 10. 总结

- **架构契合度**：三模块均已采用"工具 + 子图 + Skills + HITL + Backend 路由"四件套，结构上符合 Deep Agents v0.6 最佳实践；其中 `standard_review` 最契合（`Command` 路由 + widen 回环 + 多轨 + scope_summary）。
- **主要差距**：
  1. 节点级流式进度仅 `standard_review` 部分节点隐式实现，未形成 `ProgressBus`；
  2. 工具 / 节点缺 `RetryPolicy` + `handle_tool_errors`，长任务靠应用层 try/except；
  3. `RuntimeContext` 设计已存在但代码未落地，子图缺 `context_schema`；
  4. Subagent HITL 与主 agent HITL 重复弹窗；
  5. 子图 `compile(checkpointer=...)` 未与主图共享持久化；
  6. `breadcrumbs` 暂未落盘。
- **优化方向**：按 §7 路线 6 阶段推进，2 ~ 3 周内可完成 Phase 1 ~ 5；§6.6 / §6.7 多用户与流式能力详见 [`multi_user_runtime_design.md`](file:///d:/deep-agents/design_docs/multi_user_runtime_design.md) 与 [`stream_sse_design.md`](file:///d:/deep-agents/design_docs/stream_sse_design.md)。
