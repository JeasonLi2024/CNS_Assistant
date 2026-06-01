# Deep Agents 应用 SPEC 示例

> 用法：复制本文件为 `DEEP_AGENT_SPEC.md`，将所有 `TODO` 项替换为实际需求。后续开发以完成后的 SPEC 为准。

## 1. 项目概述

**项目名称**：TODO

**目标用户**：TODO，例如内部运营、研发、客服、投研、法务、个人知识库用户。

**核心目标**：TODO，用 1-3 句话描述这个 Deep Agent 要替用户完成什么长期、多步骤任务。

**不做范围**：TODO，明确本阶段不支持的能力，避免实现发散。

## 2. 为什么使用 Deep Agents

Deep Agents 适合复杂、多步骤、长上下文的智能体应用。它不是只做一次模型调用，而是一个 agent harness：在 LangChain 工具调用能力和 LangGraph 运行时之上，内置任务规划、文件上下文、子代理委派、长期记忆、技能加载和人工审批等能力。

本项目选择 Deep Agents 的理由：

- 任务需要分解为多个步骤，并持续追踪状态。
- Agent 需要读写文件、保存中间产物或管理长上下文。
- 需要把复杂子任务委派给隔离上下文的 subagent。
- 需要按需加载领域知识、流程规范或工具说明。
- 需要跨线程或跨会话保留项目约定、用户偏好或业务知识。
- 需要对危险操作、昂贵操作或外部副作用进行人工确认。

如果实际需求只需要一个固定工具集的一次性问答，应改用 LangChain `create_agent`；如果需要精确控制图节点、分支和循环，应考虑直接使用 LangGraph 或把 LangGraph 子流程封装为 Deep Agents 的工具/子代理。

## 3. 用户场景

### 场景 A：TODO

- **用户输入**：TODO
- **Agent 行为**：TODO
- **期望输出**：TODO
- **成功标准**：TODO

### 场景 B：TODO

- **用户输入**：TODO
- **Agent 行为**：TODO
- **期望输出**：TODO
- **成功标准**：TODO

## 4. 核心能力清单

| 能力 | 是否需要 | 说明 |
| --- | --- | --- |
| 任务规划 `write_todos` | TODO: 是/否 | 长任务是否需要显式计划和状态更新 |
| 文件系统工具 | TODO: 是/否 | 是否需要 `ls/read_file/write_file/edit_file/glob/grep` |
| 代码执行或沙箱 | TODO: 是/否 | 是否需要安装依赖、跑测试、执行 CLI |
| 自定义业务工具 | TODO: 是/否 | 需要调用哪些 API、数据库、检索系统或内部服务 |
| Skills | TODO: 是/否 | 哪些领域流程应按需加载 |
| Memory/AGENTS.md | TODO: 是/否 | 哪些规则必须每次启动都生效 |
| Subagents | TODO: 是/否 | 哪些子任务需要隔离上下文或并行处理 |
| 人工审批 | TODO: 是/否 | 哪些工具调用前必须暂停确认 |
| 结构化输出 | TODO: 是/否 | 是否需要 JSON/Pydantic schema |
| LangSmith tracing | TODO: 是/否 | 是否需要可观测性、调试和质量回放 |

## 5. Deep Agents 组件设计

### 5.1 主 Agent

**职责**：TODO，描述主 Agent 的边界，例如“理解用户目标、制定计划、调用工具、委派研究任务、汇总最终报告”。

**系统提示词要求**：

- 身份和职责：TODO
- 工作流程：TODO
- 输出风格：TODO
- 安全约束：TODO
- 失败处理：TODO

### 5.2 模型配置

**模型选择方式**：

- 快速配置：使用 `provider:model` 字符串，例如 `openai:gpt-5.4`、`anthropic:claude-sonnet-4-6`、`google_genai:gemini-3.5-flash`。
- 精细配置：使用 LangChain `init_chat_model(...)` 或具体 `ChatOpenAI`/`ChatAnthropic` 实例，再传给 `create_deep_agent(model=model)`。

**默认建议**：

```python
from langchain.chat_models import init_chat_model

model = init_chat_model(
    model="TODO: provider:model",
    temperature=0,
    timeout=60,
    max_retries=2,
)
```

**待确认项**：

- Provider：TODO
- Model：TODO
- API Key 环境变量：TODO
- 温度/随机性：TODO
- 超时与重试：TODO
- 成本预算：TODO
- 是否需要多模型路由或中途切换：TODO

### 5.3 工具

| 工具名 | 类型 | 输入 | 输出 | 副作用 | 是否审批 |
| --- | --- | --- | --- | --- | --- |
| TODO | Python callable / LangChain tool / API wrapper | TODO | TODO | TODO | TODO |

工具设计原则：

- 工具名和 docstring 必须让模型准确理解用途。
- 对外部系统写入、删除、付款、发信、执行命令等操作默认纳入审批。
- 大结果优先写入文件或由 subagent 汇总，避免污染主 Agent 上下文。

### 5.4 文件系统与后端

**后端选择**：

- `StateBackend`：默认线程内虚拟文件系统，适合原型和单线程状态。
- `FilesystemBackend`：访问本地文件系统，适合开发工具、代码仓库、文档生成；需严格权限。
- `StoreBackend`：依赖 LangGraph Store，适合跨线程持久化、托管环境或无本地文件系统场景。
- Sandbox backend：适合需要执行命令、安装依赖、跑测试但不能污染宿主机的场景。

**本项目选择**：TODO

**权限规则**：

```python
permissions = [
    {"path": "/workspace/**", "operations": ["read", "write"], "mode": "allow"},
    {"path": "**/.env*", "operations": ["read", "write"], "mode": "deny"},
]
```

待确认：

- 可读目录：TODO
- 可写目录：TODO
- 禁止访问路径：TODO
- 是否允许执行命令：TODO

### 5.5 Memory

Memory 适合放每次会话都必须生效的短规则，例如项目约定、用户偏好、安全边界。它会始终注入上下文，不适合放大量领域文档。

**AGENTS.md 内容规划**：

- 编码规范：TODO
- 业务约定：TODO
- 输出语言和格式：TODO
- 安全规则：TODO

### 5.6 Skills

Skills 适合按需加载的领域知识、操作流程、模板和脚本。每个 skill 是一个目录，必须包含 `SKILL.md`，并带 frontmatter：

```markdown
---
name: TODO
description: TODO，具体说明什么时候应该使用该 skill
---

# TODO Skill

## Instructions
TODO
```

计划的 skills：

| Skill | 触发场景 | 包含资源 | 是否给 subagent 使用 |
| --- | --- | --- | --- |
| TODO | TODO | TODO | TODO |

### 5.7 Subagents

Subagent 用来隔离长上下文、并行处理、专门化工具集。主 Agent 通过 `task` 委派，subagent 最终只返回摘要或结果文件路径。

| Subagent | 职责 | 模型 | 工具 | Skills | 输出 |
| --- | --- | --- | --- | --- | --- |
| research | TODO | TODO | TODO | TODO | TODO |
| verifier | TODO | TODO | TODO | TODO | TODO |

原则：

- 子代理不继承主 Agent 的 skills；需要显式配置。
- 子代理输出应简洁，避免返回原始大数据。
- 可把大型结果写入虚拟文件系统，再由主 Agent 按需读取。

### 5.8 人工审批与中断

需要审批的操作：

| 工具/操作 | 审批原因 | 是否允许修改输入 |
| --- | --- | --- |
| `write_file` | TODO | TODO |
| `edit_file` | TODO | TODO |
| TODO | TODO | TODO |

注意：启用 `interrupt_on` 时需要配置 checkpointer。

```python
from langgraph.checkpoint.memory import MemorySaver

checkpointer = MemorySaver()
interrupt_on = {
    "write_file": True,
    "edit_file": True,
}
```

### 5.9 结构化输出

是否需要最终输出 schema：TODO

示例：

```python
from pydantic import BaseModel, Field

class AgentResult(BaseModel):
    summary: str = Field(description="最终结论")
    artifacts: list[str] = Field(default_factory=list, description="生成文件路径")
    next_steps: list[str] = Field(default_factory=list, description="建议后续动作")
```

## 6. 初始实现草案

```python
from deepagents import create_deep_agent
from langchain.chat_models import init_chat_model
from langgraph.checkpoint.memory import MemorySaver

model = init_chat_model(
    model="TODO: provider:model",
    temperature=0,
    timeout=60,
    max_retries=2,
)

agent = create_deep_agent(
    name="TODO",
    model=model,
    system_prompt="TODO",
    tools=[
        # TODO: custom tools
    ],
    memory=[
        "./AGENTS.md",
    ],
    skills=[
        "./skills/",
    ],
    subagents=[
        # TODO: subagent configs
    ],
    permissions=[
        # TODO: filesystem permission rules
    ],
    interrupt_on={
        # TODO: approval gates
    },
    response_format=None,  # TODO: optional schema
    checkpointer=MemorySaver(),
)
```

## 7. 开发任务拆分

1. 初始化项目结构和依赖。
2. 实现主 Agent 配置。
3. 实现自定义工具及单元测试。
4. 创建 `AGENTS.md` memory 文件。
5. 创建 `skills/` 目录与首批 `SKILL.md`。
6. 配置 subagents。
7. 配置文件系统 backend、permissions 和审批策略。
8. 增加 smoke test：一次完整用户任务能跑通。
9. 增加 LangSmith tracing 配置。
10. 编写 README 和运行示例。

## 8. 验收标准

- 用户给出典型任务后，Agent 能生成计划并按计划推进。
- Agent 能正确调用业务工具，并处理工具错误。
- 文件读写只发生在允许路径内。
- 高风险操作会触发人工审批。
- 长任务不会把大结果直接塞回主上下文；必要时使用文件或 subagent 摘要。
- Skills 只在相关任务中加载，Memory 保持简短。
- 关键路径有测试或可重复运行的 smoke script。
- LangSmith tracing 可用于查看模型调用、工具调用和中断流程。

## 9. 待用户补充的问题

1. 这个 Agent 最重要的 3 个用户任务是什么？
2. 需要访问哪些外部系统、API、数据库或本地文件？
3. 哪些操作有副作用，必须审批？
4. 是否需要跨会话记住用户偏好或项目知识？
5. 是否需要多 subagent 并行工作？
6. 最终输出是自然语言、文件、JSON，还是多种组合？
7. 目标运行环境是本地脚本、服务端 API、LangGraph/LangSmith 托管，还是 Managed Deep Agents？
8. 模型 provider、预算、延迟和数据合规要求是什么？

## 10. 参考文档

- Deep Agents overview: https://docs.langchain.com/oss/python/deepagents/overview
- Harness capabilities: https://docs.langchain.com/oss/python/deepagents/harness
- Customize Deep Agents: https://docs.langchain.com/oss/python/deepagents/customization
- Context engineering: https://docs.langchain.com/oss/python/deepagents/context-engineering
- Model/provider profiles: https://docs.langchain.com/oss/python/deepagents/profiles
- Provider profiles: https://docs.langchain.com/oss/python/deepagents/models
