# 基于Deep Agents架构开发的标准文档助手

## 1. 项目概述

**项目名称**：标准文档助手

**目标用户**：负责撰写标准文档、管理标准文档的用户

**核心目标**：能够根据配套的Tools、Skills、Middlewares、sandbox以及Model（LLM）完成标准文档的检索、生成、审核、格式转化、关键信息提取等功能。

**不做范围**：暂时不考虑具体的skill设计、知识库构建、多用户鉴权、生产环境配置。当前仅实现最小功能实现完成整体智能体设计和关键节点测试即可。

## 2. 为什么使用 Deep Agents

Deep Agents 适合复杂、多步骤、长上下文的智能体应用。它不是只做一次模型调用，而是一个 agent harness：在 LangChain 工具调用能力和 LangGraph 运行时之上，内置任务规划、文件上下文、子代理委派、长期记忆、技能加载和人工审批等能力。

本项目选择 Deep Agents 的理由：

- 任务需要分解为多个步骤，并持续追踪状态。
- Agent 需要读写文件、保存中间产物或管理长上下文。
- 需要把复杂子任务委派给隔离上下文的 subagent；或者为某个特定任务设计特定的agent。
- 需要按需加载领域知识、流程规范或工具说明。
- 需要跨线程或跨会话保留项目约定、用户偏好或业务知识。
- 需要对危险操作、昂贵操作或外部副作用进行人工确认。
- 需要有良好的记忆系统设计。

如果实际需求只需要一个固定工具集的一次性问答，应改用 LangChain `create_agent`；如果需要精确控制图节点、分支和循环，应考虑直接使用 LangGraph 或把 LangGraph 子流程封装为 Deep Agents 的工具/子代理。

## 3. 用户场景

### 场景 A：标准文档审核

- **用户输入**：撰写好的标准文档（Word/PDF）
- **Agent 行为**：首先完成文档的格式转化、原文档解析，调用相关Skill完成审核
- **期望输出**：按照相应的Skill输出审核报告md
- **成功标准**：成功调用skill并输出报告

### 场景 B：标准文档生成

- **用户输入**：需要撰写新标准文档的意图内容
- **Agent 行为**：分析用户意图，进一步咨询用户获取详细信息，调用检索工具查阅现有文档内容，整理信息，最后调用文档生成Skill完成草稿撰写
- **期望输出**：用户偏好和具体需求、检索结果、标准文档草稿
- **成功标准**：能够产出符合期望的草稿

## 4. 核心能力清单

| 能力 | 是否需要 | 说明 |
| --- | --- | --- |
| 任务规划 `write_todos` | 是 | 长任务是否需要显式计划和状态更新 |
| 文件系统工具 | 是 | 是否需要 `ls/read_file/write_file/edit_file/glob/grep` |
| 代码执行或沙箱 | 是 | 是否需要安装依赖、跑测试、执行 CLI |
| 自定义业务工具 | 是 | 需要调用哪些 API、数据库、检索系统或内部服务 |
| Skills | 否 | 哪些领域流程应按需加载 |
| Memory/AGENTS.md | 是 | 哪些规则必须每次启动都生效 |
| Subagents | 是 | 哪些子任务需要隔离上下文或并行处理 |
| 人工审批 | 是 | 哪些工具调用前必须暂停确认 |
| 结构化输出 | 是 | 是否需要 JSON/Pydantic schema |
| LangSmith tracing | 是 | 是否需要可观测性、调试和质量回放 |

## 5. Deep Agents 组件设计

### 5.1 主 Agent

**职责**：主 Agent 是“标准文档助手”的编排层，不直接承载所有专业能力。它负责理解用户意图、识别任务类型、维护任务计划、选择工具或 subagent、管理文件产物、汇总最终结果，并在信息不足时主动向用户追问。

**系统提示词要求**：

- 身份和职责：主编排智能体，负责理解用户意图，下发任务给特定智能体
- 工作流程：
  1. 判断任务类型：文档审核、文档生成、格式转化、信息提取、检索问答或混合任务。
  2. 对复杂任务调用 `write_todos` 生成可追踪计划。
  3. 对用户上传或指定的 Word/PDF/Markdown 文件先做文件登记、格式识别和解析。
  4. 文档审核类任务优先走“解析 -> 审核 -> 报告生成”的链路。
  5. 文档生成类任务优先走“澄清需求 -> 检索参考 -> 结构规划 -> 草稿生成 -> 自检”的链路。
  6. 对大文本解析、检索、审核、草稿撰写等任务委派给 subagent，主 Agent 只接收摘要、结论和产物路径。
  7. 对写文件、覆盖文件、执行命令、调用外部检索或消耗较高的模型请求执行人工审批。
- 输出风格：中文优先，结论明确；过程信息按阶段展示；最终输出包含摘要、产物路径、关键发现、风险提示和下一步建议。
- 安全约束：不得伪造标准条款、来源文件或审核依据；无法确认时必须标注“不确定”并说明需要的补充材料；不得在未经确认时覆盖用户原始文件；不得读取 `.env`、密钥、凭据文件。
- 失败处理：工具失败时记录失败原因和可恢复建议；文档解析失败时尝试备用解析路径；关键信息不足时停止生成正式结论并向用户追问。

### 5.2 模型配置

**模型选择方式**：

- 本项目优先使用 Qwen3 系列语言模型或 Qwen 多模态模型。
- LangChain Python 的 Qwen 集成参考 `ChatQwen`，安装包为 `langchain-qwq`，凭据使用 `DASHSCOPE_API_KEY`。
- Deep Agents 的 `model` 参数应优先传入已初始化的 `ChatQwen` 实例，便于统一配置 `max_tokens`、`timeout`、`max_retries`、流式输出等参数。
- 对纯文本规划、审核、生成任务使用 Qwen3 语言模型；对含图片扫描件、图像化页面、表格截图等输入使用 Qwen VL 多模态模型。

**默认建议**：

```python
from langchain_qwq import ChatQwen

model = ChatQwen(
    model="qwen3-plus",
    temperature=0,
    max_tokens=8000,
    timeout=60,
    max_retries=2,
)
```

**多模态备用模型建议**：

```python
from langchain_qwq import ChatQwen

vision_model = ChatQwen(
    model="qwen-vl-max-latest",
    max_tokens=8000,
    timeout=90,
    max_retries=2,
)
```

**配置文件建议**：

优先创建 `.env` 保存密钥，创建 `config.yaml` 保存可调整配置。用户后续确认具体模型、预算和超时时间。

`.env` 示例：

```bash
DASHSCOPE_API_KEY=your_dashscope_api_key
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=your_langsmith_api_key
LANGSMITH_PROJECT=standard-document-assistant
```

`config.yaml` 示例：

```yaml
app:
  name: standard-document-assistant
  default_language: zh-CN

models:
  primary:
    provider: qwen
    class: langchain_qwq.ChatQwen
    model: qwen3-plus
    temperature: 0
    max_tokens: 8000
    timeout: 60
    max_retries: 2
  vision:
    provider: qwen
    class: langchain_qwq.ChatQwen
    model: qwen-vl-max-latest
    max_tokens: 8000
    timeout: 90
    max_retries: 2

runtime:
  streaming: true
  transport: sse
  require_human_approval: true

memory:
  checkpointer: memory
  store: inmemory
  default_thread_prefix: standard-doc
  agent_memory_namespace: standard-document-assistant
  routes:
    short_term: state
    workspace: filesystem
    long_term: store
```

**待用户确认项**：

- 主语言模型：`qwen3-plus`、`qwen3-max`、`qwen3-coder-plus` 或其他 Qwen3 系列模型。
- 多模态模型：是否启用 `qwen-vl-max-latest`。
- 单次最大输出 token、超时时间、重试次数。
- 是否允许同一任务中按阶段切换语言模型和多模态模型。
- LangSmith tracing 是否启用。

### 5.3 工具

| 工具名 | 类型 | 输入 | 输出 | 副作用 | 是否审批 |
| --- | --- | --- | --- | --- | --- |
| `parse_document` | Python callable | Word/PDF/Markdown 文件路径 | 解析后的 Markdown、元数据、页码/章节映射 | 无 | 否 |
| `convert_document_format` | Python callable / CLI wrapper | 源文件路径、目标格式 | 转换后的文件路径 | 写入新文件 | 是 |
| `extract_key_information` | Python callable / LLM tool | 文档 Markdown、抽取 schema | 标准名称、范围、术语、条款、引用文件等结构化信息 | 无 | 否 |
| `search_reference_documents` | API wrapper / retriever | 查询词、过滤条件 | 参考文档片段、来源、相似度 | 可能调用外部服务 | 是 |
| `generate_review_report` | Python callable / LLM tool | 审核发现、报告模板 | Markdown 审核报告路径 | 写入报告文件 | 是 |
| `generate_standard_draft` | Python callable / LLM tool | 用户需求、参考资料、模板 | 标准草稿 Markdown/Docx 路径 | 写入草稿文件 | 是 |
| `validate_output_schema` | Python callable | JSON 或 Pydantic 对象 | 校验结果、错误列表 | 无 | 否 |
| `propose_memory_update` | Python callable | 待记忆内容、作用域、理由 | 记忆更新提案 | 不直接写入 | 是 |

工具设计原则：

- 工具名和 docstring 必须让模型准确理解用途。
- 对外部系统写入、删除、付款、发信、执行命令等操作默认纳入审批。
- 大结果优先写入文件或由 subagent 汇总，避免污染主 Agent 上下文。
- 当前阶段只实现最小功能闭环：文档解析、基本格式转化、标准审核报告生成、标准草稿生成、结构化结果校验。
- 检索工具先预留接口，可使用本地目录检索或 mock 数据完成关键节点测试，不在本阶段建设完整知识库。

### 5.4 文件系统与后端

**后端选择**：

- `StateBackend`：默认线程内虚拟文件系统，适合原型和单线程状态。
- `FilesystemBackend`：访问本地文件系统，适合开发工具、代码仓库、文档生成；需严格权限。
- `StoreBackend`：依赖 LangGraph Store，适合跨线程持久化、托管环境或无本地文件系统场景。
- `Sandbox backend`：适合需要执行命令、安装依赖、跑测试但不能污染宿主机的场景。

**本项目选择**：

- 最小实现阶段使用 `CompositeBackend`，而不是单独使用 `FilesystemBackend`。
- `default=StateBackend()`：保留 Deep Agents 默认的线程级短期工作区，用于对话历史、临时草稿、大工具结果 offloading 和 subagent 中间文件。
- `/workspace/ -> FilesystemBackend(root_dir=<绝对路径>, virtual_mode=True)`：只把用户文档、模板和最终产物映射到真实磁盘，便于本地调试和验收。
- `/memories/ -> StoreBackend(namespace=...)`：把长期记忆文件放入 LangGraph Store，支持跨线程、跨会话读取。
- 代码执行、格式转换和测试运行应在 sandbox 或受限工作目录内完成，避免污染用户原始目录。
- 注意：官方文档要求 `FilesystemBackend.root_dir` 使用绝对路径；并建议在多数场景下用 `CompositeBackend` 包装它，避免 Deep Agents 内部的 `/large_tool_results/` 和 `/conversation_history/` 直接写入项目磁盘。

**权限规则**：

```python
permissions = [
    {"path": "/workspace/input/**", "operations": ["read"], "mode": "allow"},
    {"path": "/workspace/output/**", "operations": ["read", "write"], "mode": "allow"},
    {"path": "/workspace/tmp/**", "operations": ["read", "write"], "mode": "allow"},
    {"path": "/workspace/templates/**", "operations": ["read"], "mode": "allow"},
    {"path": "/memories/**", "operations": ["read"], "mode": "allow"},
    {"path": "**/.env*", "operations": ["read", "write"], "mode": "deny"},
    {"path": "**/*secret*", "operations": ["read", "write"], "mode": "deny"},
]
```

建议目录结构：

```text
workspace/
  input/       # 用户上传或指定的原始文档，只读
  output/      # 审核报告、草稿、转换结果
  tmp/         # 中间解析结果、缓存
  templates/   # 标准文档模板、报告模板
```

待用户确认：

- 用户原始文件是否统一复制到 `workspace/input/`。
- 输出文件是否统一写入 `workspace/output/`。
- 是否允许执行 LibreOffice、pandoc、python 脚本等格式转换命令。
- sandbox 采用本地受限目录、容器，还是后续接入 Deep Agents sandbox backend。

### 5.5 Memory

Deep Agents 的 memory 需要区分三层：短期记忆、长期记忆和持久化机制。三者不能混用。

#### 5.5.1 Short-term memory / 短期记忆

短期记忆是单个 `thread_id` 内的工作状态，包括当前对话历史、任务计划、临时文件、中间解析结果、大工具输出引用等。它由 LangGraph checkpointer 和 Deep Agents 的 `StateBackend` 管理。

本项目短期记忆设计：

- 使用 `StateBackend()` 作为 `CompositeBackend.default`。
- 所有临时中间产物默认写入 `/tmp/` 或 Deep Agents 内部路径，例如 `/large_tool_results/`、`/conversation_history/`。
- 同一个用户任务必须使用稳定的 `thread_id`，否则同一会话内的历史、审批暂停点和临时文件无法恢复。
- subagent 写入 `StateBackend` 的文件会在当前线程内继续对主 Agent 和其他 subagent 可见，因此 subagent 应只写必要摘要、结构化结果和产物引用。
- 短期记忆不用于跨会话保存用户偏好、项目规则或审核经验。

适合放入短期记忆的内容：

- 当前文档的解析 Markdown。
- 本次审核发现的中间 JSON。
- 本次草稿生成的章节规划。
- 本次工具调用的大结果引用。
- 当前 run 的审批状态和 SSE 事件状态。

不适合放入短期记忆的内容：

- 用户长期偏好。
- 项目固定规则。
- 通用标准审核流程。
- 可跨文档复用的模板和经验。

#### 5.5.2 Long-term memory / 长期记忆

长期记忆是跨线程、跨会话可复用的信息。Deep Agents 官方推荐通过 filesystem-backed memory 实现：Agent 以文件形式读取和更新 memory，由 backend 决定这些文件存在哪里。对于本项目，长期记忆应放在 `/memories/` 路径，并由 `StoreBackend` 持久化。

本项目长期记忆设计：

- `memory=["/memories/AGENTS.md", "/memories/preferences.md"]`。
- `/memories/AGENTS.md`：Agent 级长期规则，包含标准文档助手的通用行为准则、安全边界、输出要求。
- `/memories/preferences.md`：用户或当前使用者的偏好，例如报告详略、默认输出格式、常用标准类型。
- `/memories/project-notes.md`：可选，保存本项目后续沉淀的非敏感经验，例如常见审核问题分类。
- 长期记忆应短小、稳定、可审计，不保存整篇标准文档、敏感文件内容、API Key、个人隐私或未经确认的模型推断。
- 长期记忆更新必须经过人工审批或后台 consolidation 流程，不允许模型在用户无感知情况下随意改写核心规则。

长期记忆作用域：

- 当前阶段不做多用户鉴权，默认采用 agent-scoped memory，即所有会话共享 `("standard-document-assistant", "memories")` 命名空间。
- 如果后续进入多用户场景，必须改为 user-scoped memory，例如 namespace 使用 `(user_id, "memories")`，避免用户偏好和文档上下文互相泄露。
- 组织级政策、审核规范、模板规则不建议写入用户级长期记忆，应作为只读 skills、模板文件或 policies 路径管理。

#### 5.5.3 Persistence / 持久化

持久化分两类：graph state persistence 和 memory/file persistence。

**Graph state persistence**：

- 由 checkpointer 负责，保存 LangGraph/Deep Agents 每个 super-step 的状态。
- 开发阶段可用 `MemorySaver` 或 SQLite checkpointer。
- 生产阶段应使用 Postgres checkpointer，避免进程重启后丢失 run 状态。
- 每次调用必须提供 `thread_id`：`config={"configurable": {"thread_id": "..."}}`。
- SSE 流式输出和人工审批依赖 run 可恢复；如果触发 `interrupt_on`，恢复时必须使用同一个 `thread_id` 和 checkpoint。

**Memory/file persistence**：

- `/workspace/`：真实磁盘文件，保存用户输入副本、输出报告、草稿和格式转换结果。
- `/memories/`：`StoreBackend` 文件，保存长期记忆，跨线程可用。
- `StateBackend` 默认路径：保存短期会话文件、工具结果 offloading 和内部对话历史，线程内可用。
- 后续如果部署到 LangSmith Deployment，可省略本地 store 参数，使用平台自动 provision 的 store；如果自部署，生产环境应使用 Postgres/Redis/cloud BaseStore，而不是 `InMemoryStore`。

#### 5.5.4 Memory 与 Skills 的边界

- Memory 总是注入或可被读取，适合短规则、偏好和关键约束。
- Skills 使用 progressive disclosure，适合较长的审核流程、生成流程、模板说明和领域知识。
- 标准全文、审核模板、示例报告不应放进 Memory；应放入 `/workspace/templates/`、检索系统或后续正式 skill。
- 用户确认过的稳定偏好可以进入 `/memories/preferences.md`；单次任务偏好只留在 `thread_id` 对应的短期记忆中。

**AGENTS.md 内容规划**：

- 编码规范：Python 优先；工具函数必须有清晰 docstring、类型标注和可测试输入输出；配置从 `.env` 或 `config.yaml` 读取。
- 业务约定：所有标准文档相关输出默认使用中文；审核结论必须引用来源章节、页码或解析片段；无法定位来源时标记为“依据不足”。
- 输出语言和格式：过程性结果使用简洁中文；最终产物优先输出 Markdown，必要时再转换为 Docx/PDF。
- 安全规则：不得读取密钥文件；不得覆盖用户原始文档；外部调用、文件写入、命令执行需审批；模型不得编造标准条文或参考文献。
- 阶段约束：当前不实现完整知识库、多用户鉴权和生产部署，仅实现最小可用智能体设计与关键节点测试。

建议初始长期记忆文件：

```text
/memories/AGENTS.md
/memories/preferences.md
/memories/project-notes.md
```

### 5.6 Skills

虽然第 4 节当前将 Skills 标记为“否”，但第 1-3 节中的审核和生成流程都提到“调用相关 Skill”。为不改变用户已填写内容，本阶段按如下策略处理：

- 最小实现阶段不设计完整、正式的 skill 体系。
- 先预留 `skills/` 目录和 `SKILL.md` 格式，便于后续扩展。
- 当前关键节点测试可用轻量 mock skill 或普通工具函数模拟“审核 Skill”和“文档生成 Skill”的调用边界。
- 当用户确认具体标准类型、审核规则和模板后，再把 mock skill 固化为正式 skill。

每个正式 skill 是一个目录，必须包含 `SKILL.md`，并带 frontmatter：

```markdown
---
name: standard-review
description: 当用户要求审核标准文档的结构、格式、术语、引用文件、条款一致性和可追溯性时使用。
---

# Standard Review Skill

## Instructions
根据传入的文档解析结果和审核规则，输出结构化审核发现，并生成 Markdown 审核报告。
```

计划的 skills：

| Skill | 触发场景 | 包含资源 | 是否给 subagent 使用 |
| --- | --- | --- | --- |
| `standard-review` | 用户上传标准文档并要求审核 | 审核清单、报告模板、问题分级规则 | 给 `reviewer` 使用 |
| `standard-drafting` | 用户要求生成新标准草稿 | 标准结构模板、章节写作规范、草稿模板 | 给 `writer` 使用 |
| `standard-extraction` | 用户要求提取关键信息 | 字段 schema、术语表、引用文件抽取规则 | 给 `extractor` 使用 |

### 5.7 Subagents

Subagent 用来隔离长上下文、并行处理、专门化工具集。主 Agent 通过 `task` 委派，subagent 最终只返回摘要或结果文件路径。

| Subagent | 职责 | 模型 | 工具 | Skills | 输出 |
| --- | --- | --- | --- | --- | --- |
| `parser` | 解析 Word/PDF，生成 Markdown 和结构元数据 | Qwen3 或非 LLM 工具优先 | `parse_document`, `convert_document_format` | 暂无 | 解析文件路径、解析摘要、失败页码 |
| `extractor` | 提取标准名称、范围、术语、引用文件、条款结构 | Qwen3 | `extract_key_information`, `validate_output_schema` | `standard-extraction` | 结构化 JSON、字段置信度 |
| `reviewer` | 对标准文档进行格式、结构、内容一致性审核 | Qwen3 | `generate_review_report` | `standard-review` | Markdown 审核报告路径、问题列表 |
| `research` | 检索参考标准或已有文档，提供依据摘要 | Qwen3 | `search_reference_documents` | 后续扩展 | 检索摘要、来源列表 |
| `writer` | 根据用户意图、澄清信息和参考资料生成草稿 | Qwen3 | `generate_standard_draft` | `standard-drafting` | 标准草稿路径、待确认问题 |
| `vision_parser` | 处理扫描件、图片页、表格截图等多模态输入 | Qwen VL | `parse_document` | 暂无 | 图片/页面理解结果 |

原则：

- 子代理不继承主 Agent 的 skills；需要显式配置。
- 子代理输出应简洁，避免返回原始大数据。
- 可把大型结果写入虚拟文件系统，再由主 Agent 按需读取。
- 最小实现阶段至少实现 `parser`、`reviewer`、`writer` 三个逻辑边界；可以先作为 subagent 配置或工具函数模拟，后续再细分。

### 5.8 人工审批与中断

需要审批的操作：

| 工具/操作 | 审批原因 | 是否允许修改输入 |
| --- | --- | --- |
| `write_file` | 生成审核报告、草稿、转换结果会写入文件 | 是 |
| `edit_file` | 可能修改已有产物，需防止覆盖原始文档 | 是 |
| `convert_document_format` | 可能调用外部命令并生成新文件 | 是 |
| `search_reference_documents` | 可能调用外部检索服务或消耗额度 | 是 |
| `propose_memory_update` | 长期记忆会影响后续会话，需要用户确认 | 是 |
| sandbox `execute` | 命令执行有副作用和安全风险 | 是 |

注意：启用 `interrupt_on` 时需要配置 checkpointer。

```python
from langgraph.checkpoint.memory import MemorySaver

checkpointer = MemorySaver()
interrupt_on = {
    "write_file": True,
    "edit_file": True,
    "convert_document_format": True,
    "search_reference_documents": True,
    "propose_memory_update": True,
    "execute": True,
}
```

### 5.9 结构化输出

需要结构化输出。原因是标准文档审核、信息抽取和前端流式展示都需要稳定字段，不能只依赖自然语言。

示例：

```python
from pydantic import BaseModel, Field

class Artifact(BaseModel):
    path: str = Field(description="产物文件路径")
    type: str = Field(description="artifact 类型，例如 review_report、draft、converted_doc、extracted_json")
    description: str = Field(description="产物说明")

class Finding(BaseModel):
    severity: str = Field(description="问题级别：critical/high/medium/low/info")
    location: str = Field(description="章节、页码或原文片段位置")
    issue: str = Field(description="发现的问题")
    suggestion: str = Field(description="修改建议")
    evidence: str = Field(description="审核依据或引用片段")

class AgentResult(BaseModel):
    summary: str = Field(description="最终结论")
    task_type: str = Field(description="任务类型：review/draft/convert/extract/search/mixed")
    artifacts: list[Artifact] = Field(default_factory=list, description="生成文件")
    findings: list[Finding] = Field(default_factory=list, description="审核发现")
    next_steps: list[str] = Field(default_factory=list, description="建议后续动作")
```

### 5.10 流式输出 / SSE 设计

本项目需要支持流式输出，前端或调用方应能实时看到 Agent 的阶段进展、模型 token、工具调用、审批请求和最终产物。

**传输方式**：HTTP Server-Sent Events，接口建议为：

```text
POST /runs
GET  /runs/{run_id}/events
```

或合并为：

```text
POST /chat/stream
Accept: text/event-stream
```

**事件类型设计**：

| event | 触发时机 | data 字段 |
| --- | --- | --- |
| `run.started` | 创建运行 | `run_id`, `thread_id`, `task_type` |
| `plan.updated` | `write_todos` 更新计划 | `todos` |
| `message.delta` | 模型 token 级输出 | `delta`, `agent_name` |
| `tool.started` | 工具调用开始 | `tool_name`, `args_summary` |
| `tool.completed` | 工具调用完成 | `tool_name`, `result_summary`, `artifact_paths` |
| `subagent.started` | 子代理开始 | `subagent_name`, `task` |
| `subagent.completed` | 子代理完成 | `subagent_name`, `summary`, `artifact_paths` |
| `approval.required` | 命中 `interrupt_on` | `tool_name`, `proposed_args`, `reason` |
| `artifact.created` | 生成文件 | `path`, `type`, `description` |
| `run.completed` | 任务完成 | `result` |
| `run.failed` | 任务失败 | `error`, `recoverable`, `next_action` |

**SSE 数据格式示例**：

```text
event: message.delta
data: {"run_id":"r_001","agent_name":"writer","delta":"正在生成标准草稿的范围章节..."}

event: artifact.created
data: {"run_id":"r_001","path":"workspace/output/draft.md","type":"draft","description":"标准文档草稿"}
```

**后端实现建议**：

- Deep Agents 调用层使用 async streaming，逐步消费模型、工具和图运行事件。
- 将 LangGraph/Deep Agents 的内部事件映射为业务 SSE 事件，不直接把底层事件结构暴露给前端。
- 对工具参数做脱敏，SSE 中不得输出 API Key、文件绝对路径中的敏感信息或完整大文本。
- 大文本内容写入 artifact 文件，SSE 只传摘要和路径。
- 审批事件需要暂停 run，等待用户通过审批接口提交 `approve/reject/modify`。

**审批接口建议**：

```text
POST /runs/{run_id}/approvals/{approval_id}
```

请求体：

```json
{
  "action": "approve",
  "modified_args": null,
  "comment": "允许生成审核报告"
}
```

## 6. 初始实现草案

```python
from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend, StoreBackend
from langchain_qwq import ChatQwen
from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.memory import InMemoryStore
from dotenv import load_dotenv

load_dotenv()

workspace_root = Path("./workspace").resolve()

model = ChatQwen(
    model="qwen3-plus",
    temperature=0,
    max_tokens=8000,
    timeout=60,
    max_retries=2,
)

checkpointer = MemorySaver()
store = InMemoryStore()

backend = CompositeBackend(
    default=StateBackend(),
    routes={
        "/workspace/": FilesystemBackend(
            root_dir=str(workspace_root),
            virtual_mode=True,
        ),
        "/memories/": StoreBackend(
            namespace=lambda _rt: ("standard-document-assistant", "memories"),
        ),
    },
)

agent = create_deep_agent(
    name="standard-document-assistant",
    model=model,
    system_prompt="你是标准文档助手的主编排智能体，负责标准文档检索、生成、审核、格式转化和关键信息提取。",
    tools=[
        parse_document,
        convert_document_format,
        extract_key_information,
        search_reference_documents,
        generate_review_report,
        generate_standard_draft,
        validate_output_schema,
        propose_memory_update,
    ],
    memory=[
        "/memories/AGENTS.md",
        "/memories/preferences.md",
    ],
    skills=[
        "./skills/",
    ],
    subagents=[
        parser_subagent,
        extractor_subagent,
        reviewer_subagent,
        research_subagent,
        writer_subagent,
    ],
    backend=backend,
    permissions=[
        {"path": "/workspace/input/**", "operations": ["read"], "mode": "allow"},
        {"path": "/workspace/output/**", "operations": ["read", "write"], "mode": "allow"},
        {"path": "/workspace/tmp/**", "operations": ["read", "write"], "mode": "allow"},
        {"path": "/workspace/templates/**", "operations": ["read"], "mode": "allow"},
        {"path": "/memories/**", "operations": ["read"], "mode": "allow"},
        {"path": "**/.env*", "operations": ["read", "write"], "mode": "deny"},
    ],
    interrupt_on={
        "write_file": True,
        "edit_file": True,
        "convert_document_format": True,
        "search_reference_documents": True,
        "propose_memory_update": True,
        "execute": True,
    },
    response_format=AgentResult,
    checkpointer=checkpointer,
    store=store,
)

config = {"configurable": {"thread_id": "standard-doc-session-001"}}
```

说明：

- `StateBackend()` 是短期记忆和内部工作区，线程内持久，跨线程不共享。
- `/workspace/` 映射到真实磁盘，用于输入、输出、模板和临时文件。
- `/memories/` 映射到 `StoreBackend`，用于长期记忆；初次运行前需要 seed `/memories/AGENTS.md` 和 `/memories/preferences.md`。
- `MemorySaver` 仅适合最小实现和测试。进入生产环境后需要替换为持久化 checkpointer。
- 每次 invoke/stream 都必须传入稳定 `thread_id`，审批恢复和 SSE run 恢复依赖它。

## 7. 开发任务拆分

1. 初始化项目结构和依赖。
2. 创建 `.env.example` 和 `config.yaml`，接入 Qwen `ChatQwen` 配置。
3. 实现主 Agent 配置和 `AgentResult` 等 Pydantic schema。
4. 实现 `CompositeBackend`：`StateBackend` 负责短期记忆，`FilesystemBackend` 负责 `/workspace/`，`StoreBackend` 负责 `/memories/`。
5. seed `/memories/AGENTS.md` 和 `/memories/preferences.md`，写入标准文档助手的最小长期记忆。
6. 实现稳定 `thread_id` 策略，保证同一 run 的 SSE、审批和恢复使用同一线程。
7. 实现最小工具集：文档解析、格式转换、信息抽取、审核报告生成、草稿生成、schema 校验。
8. 配置 `parser/reviewer/writer` 三个最小 subagent，预留 `extractor/research/vision_parser`。
9. 配置文件系统 backend、workspace 目录、permissions 和审批策略。
10. 实现 SSE 流式输出接口和事件映射。
11. 实现审批接口，支持 approve/reject/modify，并能从 checkpoint 恢复。
12. 增加 smoke test：同一 `thread_id` 下短期记忆可恢复。
13. 增加 smoke test：不同 `thread_id` 可读取 `/memories/` 长期记忆，但不共享短期临时文件。
14. 增加 smoke test：标准文档审核链路跑通。
15. 增加 smoke test：标准文档生成链路跑通。
16. 增加 LangSmith tracing 配置。
17. 编写 README 和运行示例。

## 8. 验收标准

- 用户给出典型任务后，Agent 能生成计划并按计划推进。
- Agent 能正确调用业务工具，并处理工具错误。
- 文件读写只发生在允许路径内。
- 高风险操作会触发人工审批。
- 长任务不会把大结果直接塞回主上下文；必要时使用文件或 subagent 摘要。
- 当前阶段允许用 mock skill 或工具函数模拟审核/生成 Skill 边界，但必须保留后续正式 Skill 扩展位置。
- Memory 保持简短，只放全局规则，不放大段标准正文。
- 关键路径有测试或可重复运行的 smoke script。
- LangSmith tracing 可用于查看模型调用、工具调用和中断流程。
- SSE 能持续输出 `run.started`、`plan.updated`、`message.delta`、`tool.started`、`tool.completed`、`approval.required`、`artifact.created`、`run.completed` 或 `run.failed`。
- Qwen 模型配置从 `.env` 和 `config.yaml` 读取，用户不需要改源码即可切换主要模型。
- 标准文档审核场景能输出 Markdown 审核报告。
- 标准文档生成场景能输出用户偏好/需求摘要、检索结果摘要和标准文档草稿。
- 短期记忆只在同一 `thread_id` 内恢复；换线程后不应看到上一线程的 `/tmp/` 中间文件。
- 长期记忆通过 `/memories/` 和 `StoreBackend` 跨线程可读；默认 agent-scoped，后续多用户场景必须改成 user-scoped namespace。
- `FilesystemBackend` 不单独作为全局 backend 使用，必须由 `CompositeBackend` 路由 `/workspace/`，并启用 `virtual_mode=True`。
- 开发阶段可以使用 `MemorySaver` 和 `InMemoryStore`；文档中明确它们不是生产持久化方案。

## 9. 待用户补充的问题

1. 主语言模型最终选择哪个 Qwen3 系列模型：`qwen3-plus`、`qwen3-max`、`qwen3-coder-plus` 或其他？
2. 是否启用 Qwen VL 多模态模型处理扫描件、图片页和表格截图？
3. 第一阶段标准文档审核需要覆盖哪些规则：格式、结构、术语、引用文件、条款一致性、可执行性，还是全部？
4. 第一阶段标准文档生成是否需要固定模板？模板文件由用户提供还是先用内置最小模板？
5. 检索工具第一阶段使用本地目录、mock 数据，还是接入真实知识库/API？
6. 文件格式转换第一阶段支持哪些格式：Word -> Markdown、PDF -> Markdown、Markdown -> Word、Markdown -> PDF？
7. SSE 的调用方是 Web 前端、命令行客户端，还是第三方系统？
8. 审批操作由谁完成：当前用户、管理员，还是后续接入权限系统？
9. 是否要求所有中间产物都保留，还是任务结束后清理 `tmp/`？
10. LangSmith tracing 是否默认开启？
11. 长期记忆当前是否采用 agent-scoped，还是提前预留 user-scoped namespace？
12. 生产化时 checkpointer 和 store 计划使用 Postgres、Redis、LangSmith Deployment 平台 store，还是其他 BaseStore 实现？
13. 哪些信息允许写入 `/memories/preferences.md`，哪些必须只保留在当前线程短期记忆中？

## 10. 参考文档

- Deep Agents overview: https://docs.langchain.com/oss/python/deepagents/overview
- Harness capabilities: https://docs.langchain.com/oss/python/deepagents/harness
- Customize Deep Agents: https://docs.langchain.com/oss/python/deepagents/customization
- Context engineering: https://docs.langchain.com/oss/python/deepagents/context-engineering
- Memory: https://docs.langchain.com/oss/python/deepagents/memory
- Backends: https://docs.langchain.com/oss/python/deepagents/backends
- Model/provider profiles: https://docs.langchain.com/oss/python/deepagents/profiles
- Provider profiles: https://docs.langchain.com/oss/python/deepagents/models
- ChatQwen integration: https://docs.langchain.com/oss/python/integrations/chat/qwen
