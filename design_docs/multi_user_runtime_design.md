# 多用户 Runtime 与部署设计

> 适用框架：Deep Agents v0.6 + LangGraph Server / LangSmith Deployment。
> 适用代码：`d:\deep-agents\src\standard_document_assistant\agent.py`、`constants.py`、`pathing.py`、`uploads.py`、`artifacts.py`、`langgraph.json`、`config.yaml`。
> 设计目标：
> 1. 业务侧（标准文档助理）支持多租户 / 多用户：每个用户的 thread、文件、产物、审核记录、长记忆完全隔离；
> 2. 用 LangGraph 的 `Runtime[Context]`、`runtime.server_info`、LangSmith Deployment 自动注入的 `assistant_id / user` 实现零硬编码的多用户适配；
> 3. 持久化从本地 `MemorySaver + InMemoryStore` 演进到 `PostgresSaver + PostgresStore + pgvector`，并支持 Redis / 云上托管 store；
> 4. 与 LangGraph Server v2 + LangSmith Deployment API 兼容，部署形态可插拔。

---

## 1. 现状与缺口

| 关注点 | 现状 | 缺口 |
| --- | --- | --- |
| Checkpointer | 本地 `MemorySaver`（进程内）；`langgraph_server=True` 时交给平台。 | 无 Postgres / Sqlite fallback；本地重启会丢所有 thread 状态。 |
| Store | 本地 `InMemoryStore` + `seed_memory_store`；部署时由 `StoreBackend` 接管。 | Store backend 的 namespace 已实现按 `(assistant_id, user_id)` 隔离（见 `_memory_namespace_factory`），但 `_memory_namespace_factory` 还未真正被消费。 |
| FilesystemBackend | `/workspace/` 在本地直接挂载到 `WORKSPACE_ROOT`；`langgraph_server=True` 时默认关闭。 | 部署时无对象存储（S3 / OSS）映射，文件无法跨副本共享。 |
| thread_id 策略 | 简单字符串（CLI 默认 `standard-doc-session-001`）；`build_thread_config` 透传。 | 缺统一格式（`tenant:user:session[:branch]`），缺规范化校验。 |
| 多用户隔离 | 仅 namespace 层；workspace 文件、uploads、artifacts 仍写共享目录。 | 缺：用户维度 uploads / outputs / reviews 子目录；缺文件路径白名单校验。 |
| User 身份来源 | `runtime.server_info.user`（部署时平台注入）；本地运行取不到。 | 本地调试无身份；缺 dev-mode 兜底（从 env / token 取）。 |
| Runtime Context | 未使用 `context_schema` / `context` 显式注入；只靠 `config["configurable"]` 临时字段。 | 缺 `Context` dataclass；缺节点 / 工具内 `runtime.context` 访问。 |
| Trace ID | 已有 `invoke_traced_graph` 透传 `run_name / tags / metadata`。 | 缺应用层 `trace_id` 显式构造（`run_xxx_xxx`）与跨子图唯一性。 |
| 限流 / 并发 | 无。 | 多用户场景下需要：thread 级 LLM 并发上限、租户级 QPS 配额。 |
| 部署清单 | `langgraph.json` 已声明 `agent` + `metadata_extraction` 两图。 | 缺 `auth` / `http.configurable_headers` / `checkpointer` / `store` 显式声明。 |
| 监控 | LangSmith 透传。 | 缺应用层 `breadcrumbs`（重要事件：HITL / widen / aggregate 完成）汇总。 |

---

## 2. Runtime 与 Context 模型

### 2.1 显式定义 Context

```python
# src/standard_document_assistant/runtime/context.py
from dataclasses import dataclass, field
from typing import Any

@dataclass(frozen=True)
class TenantContext:
    tenant_id: str                # 租户/组织
    user_id: str                  # 用户
    roles: tuple[str, ...] = ()   # 角色（RBAC）
    plan: str = "default"         # 计费套餐
    request_id: str = ""          # 关联外部审计 ID

@dataclass(frozen=True)
class RuntimeContext:
    """静态上下文：注入到 LangGraph Runtime，节点/工具/runtime.context 访问。"""
    tenant: TenantContext
    trace_id: str = ""            # 应用层 trace id（与 LangSmith run_id 不同）
    assistant_id: str = ""        # 来源 assistant（platform 注入或本地默认）
    graph_id: str = ""            # 来源 graph（agent / metadata_extraction）
    started_at: str = ""          # ISO 时间
    request_ip: str = ""          # 审计
    feature_flags: dict[str, bool] = field(default_factory=dict)
```

### 2.2 在 Graph 中声明 context_schema

```python
# graphs/standard_review/graph.py
from typing import TypedDict
from langgraph.graph import StateGraph

class StandardReviewRuntime(TypedDict, total=False):
    tenant_id: str
    user_id: str
    trace_id: str
    request_id: str

# 编译时
graph = builder.compile(
    name=STANDARD_REVIEW_GRAPH_NAME,
    context_schema=StandardReviewRuntime,
)
```

> 顶层 `create_deep_agent` 同样支持 `context_schema`；建议顶层 schema 与子图 schema 等价或子集。

### 2.3 节点 / 工具 / 中间件中访问

```python
# 节点
def ingest(state: StandardReviewState, runtime: Runtime) -> dict:
    ctx: RuntimeContext = runtime.context
    tenant = ctx.tenant.tenant_id
    user = ctx.tenant.user_id
    log.info("ingest start", extra={"tenant": tenant, "user": user, "trace_id": ctx.trace_id})
    ...

# 工具（StructuredTool）
def run_standard_review(runtime: ToolRuntime, **kwargs):
    ctx = runtime.context  # RuntimeContext
    tenant = ctx.tenant.tenant_id
    ...

# Middleware
class TenantAwareMiddleware(AgentMiddleware):
    def before_agent(self, state, runtime: Runtime) -> dict:
        ctx = runtime.context
        # 在系统 prompt 注入 tenant 标识（仅模型可读）
        return {"messages": [SystemMessage(content=f"[tenant={ctx.tenant.tenant_id};user={ctx.tenant.user_id}]")]}
```

### 2.4 invoke 端构造 Context

```python
# server 层（FastAPI / LangGraph SDK）
from standard_document_assistant.runtime.context import RuntimeContext, TenantContext

def build_context(request) -> RuntimeContext:
    auth = request.state.auth  # 自定义 auth 中间件注入
    trace_id = request.headers.get("X-Trace-Id") or f"tr_{uuid.uuid4().hex[:12]}"
    return RuntimeContext(
        tenant=TenantContext(tenant_id=auth.tenant_id, user_id=auth.user_id, roles=auth.roles),
        trace_id=trace_id,
        request_id=trace_id,
        request_ip=request.client.host,
    )

result = agent.invoke(
    {"messages": [{"role": "user", "content": payload}]},
    config={
        "configurable": {"thread_id": build_thread_id(request)},
    },
    context=build_context(request),  # ← 关键
)
```

> 在 LangGraph Server 部署时，`context` 仍可传；`runtime.server_info` 自动提供 `assistant_id` / `user`。本地运行时 `server_info is None`，需要兜底。

### 2.5 runtime.server_info 兜底

```python
def resolve_assistant_and_user(runtime: Runtime) -> tuple[str, str | None]:
    server = getattr(runtime, "server_info", None)
    if server is not None:
        user = getattr(server, "user", None)
        user_id = getattr(user, "identity", None) if user is not None else None
        return getattr(server, "assistant_id", AGENT_NAME), user_id
    # 本地兜底：从 env / 临时 token 取
    return os.getenv("STANDARD_DOC_ASSISTANT_ID", AGENT_NAME), os.getenv("STANDARD_DOC_DEV_USER_ID")
```

### 2.6 Runtime 依赖图

```
┌──────────────────────────────────────────────┐
│ LangGraph Runtime                            │
│  - context: RuntimeContext（静态）            │
│  - store: BaseStore（长记忆 / 跨 thread）     │
│  - stream_writer（custom 通道）               │
│  - execution_info: {thread_id, run_id, attempt}│
│  - server_info: {assistant_id, graph_id, user}│
└──────────────────────────────────────────────┘
         ▲                                 ▲
         │                                 │
┌────────────────────────┐    ┌────────────────────────┐
│ 节点 / 工具 / Middleware│    │ LangSmith trace        │
│  通过 ToolRuntime      │    │ （run_name / tags）    │
│  访问 context / store  │    │                        │
└────────────────────────┘    └────────────────────────┘
```

---

## 3. thread_id 命名规范

### 3.1 格式

```
thread_id = "<tenant_id>:<user_id>:<session_id>[:<branch_id>]"
```

- `tenant_id` / `user_id` 用 `safe_name` 强制为 `[A-Za-z0-9_.-]+`，避免路径穿越；
- `session_id` 客户端生成（UUID 短码）；
- `branch_id` 可选，用于 sub-conversation（同一个 thread 派生 HITL 多次确认）。

### 3.2 构造与校验

```python
# src/standard_document_assistant/runtime/threading.py
import re
import uuid
from dataclasses import dataclass

_THREAD_PART = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

@dataclass(frozen=True)
class ThreadId:
    tenant_id: str
    user_id: str
    session_id: str
    branch_id: str = ""

    def __post_init__(self):
        for part in (self.tenant_id, self.user_id, self.session_id, self.branch_id):
            if part and not _THREAD_PART.match(part):
                raise ValueError(f"thread_id 段非法: {part!r}")

    def __str__(self) -> str:
        base = f"{self.tenant_id}:{self.user_id}:{self.session_id}"
        return f"{base}:{self.branch_id}" if self.branch_id else base

    @classmethod
    def build(cls, tenant_id: str, user_id: str, *, session_id: str | None = None, branch_id: str = "") -> "ThreadId":
        return cls(tenant_id, user_id, session_id or f"s_{uuid.uuid4().hex[:12]}", branch_id)

    @classmethod
    def parse(cls, raw: str) -> "ThreadId":
        parts = raw.split(":")
        if len(parts) < 3 or len(parts) > 4:
            raise ValueError(f"thread_id 段数非法: {raw}")
        return cls(parts[0], parts[1], parts[2], parts[3] if len(parts) == 4 else "")

    @property
    def key(self) -> str:
        return f"{self.tenant_id}/{self.user_id}/{self.session_id}"  # 用于 uploads/outputs 子目录
```

### 3.3 应用层消费

- `build_thread_config(thread_id)`：解析 `ThreadId`，只把 `session_id` 之后的部分暴露给 LangGraph `configurable.thread_id`（避免把租户信息透到第三方 trace 平台）。
- uploads / outputs / artifacts 路径直接以 `key` 划分子目录。
- StoreBackend namespace 不依赖 thread_id（靠 namespace factory），但 `langgraph.configurable.thread_id` 用于 checkpointer 隔离。

---

## 4. 多用户文件隔离

### 4.1 目录结构

```
workspace/
  input/
    uploads/
      <tenant>/<user>/<session>/
        foo.pdf
        upload_manifest.json
  output/
    mineru/
      <tenant>/<user>/<session>/<file_stem>/
        auto/...
    metadata/
      <tenant>/<user>/<session>/<file_stem>/
        json/...
    reviews/
      <tenant>/<user>/<session>/<file_stem>/
        reports/...
    reports/
    drafts/
    artifacts/
      <thread_id_safe>/
        artifact_manifest.json
```

### 4.2 路径工厂

```python
# src/standard_document_assistant/runtime/paths.py
def user_uploads_dir(thread: ThreadId) -> Path:
    return UPLOADS_DIR / thread.tenant_id / thread.user_id / thread.session_id

def user_output_dir(thread: ThreadId, kind: str) -> Path:
    safe = safe_name(kind, fallback="misc")
    return OUTPUT_DIR / safe / thread.tenant_id / thread.user_id / thread.session_id

def user_artifact_dir(thread_id: str) -> Path:
    return ARTIFACTS_DIR / safe_name(thread_id, fallback="thread")
```

### 4.3 resolve_workspace_read_path / write_path 升级

```python
def resolve_workspace_read_path(
    file_path: str,
    *,
    thread: ThreadId | None,
    allowed_roots: Iterable[Path] | None = None,
    suffixes: set[str] | None = None,
) -> tuple[Path, str]:
    host, virtual = _resolve_workspace_path(file_path)  # 现状逻辑
    # 强制把 allowed_roots 限定到当前用户的子目录
    user_roots = [user_uploads_dir(thread), user_output_dir(thread, "mineru"), user_input_dir()] if thread else list(allowed_roots or [])
    return ensure_within(host, user_roots, purpose="读取"), virtual
```

> 关键：业务工具（`parse_file_with_mineru`、`extract_standard_metadata`、`run_standard_review` 等）**必须**传入 `thread`，由调用方在工具内部从 `runtime.context` 解析。

### 4.4 Tools 透传 thread

```python
@tool
def parse_file_with_mineru(
    runtime: ToolRuntime,
    file_path: str,
    ...
) -> dict[str, Any]:
    ctx: RuntimeContext = runtime.context
    thread = ThreadId.parse(runtime.config["configurable"]["thread_id"])
    host, virtual = resolve_workspace_read_path(file_path, thread=thread, suffixes={".pdf", ".docx"})
    ...
```

---

## 5. Store 命名空间与持久化

### 5.1 命名空间规范

```
namespace:
  memories:     (assistant_id, user_id)             # 每个用户/assistant 独立的长期记忆
  agent_assets: (assistant_id,)                     # 共享但按 assistant 切分（规则、模板）
  review_cache: (assistant_id, user_id, "reviews")  # 跨 thread 的审核结果索引
  run_meta:     (assistant_id, user_id, "runs")     # 历史 run 元数据，便于回放
```

### 5.2 实现

`_memory_namespace_factory` / `_agent_namespace_factory` 已存在（[`agent.py:54-86`](file:///d:/deep-agents/src/standard_document_assistant/agent.py#L54-L86)）。需要：
1. 把 `langgraph_server` 分支优先用 `server_info` 解析；
2. 加上 `(assistant_id, user_id, "memories")` 段；
3. 增加 `review_cache` / `run_meta` 工厂。

```python
def make_namespace(*, kind: str):
    def _factory(rt: Runtime) -> tuple[str, ...]:
        assistant_id, user_id = resolve_assistant_and_user(rt)
        base = (assistant_id,) if not user_id else (assistant_id, user_id)
        if kind == "memories":
            return (*base, "memories")
        if kind == "agent_assets":
            return (assistant_id, "assets")
        if kind == "review_cache":
            return (*base, "reviews")
        if kind == "run_meta":
            return (*base, "runs")
        return base
    return _factory

routes = {
    "/memories/": StoreBackend(namespace=make_namespace(kind="memories")),
    "/skills/":   StoreBackend(namespace=make_namespace(kind="agent_assets")) if langgraph_server else FilesystemBackend(...),
    "/workspace/": ...  # 见 §4
}
```

### 5.3 持久化选型

| 部署形态 | Checkpointer | Store | FilesystemBackend |
| --- | --- | --- | --- |
| 本地（`langgraph dev`） | `MemorySaver`（现状） | `InMemoryStore` | `FilesystemBackend(root_dir=WORKSPACE_ROOT)`（现状） |
| 本地持久 | `SqliteSaver`（`./checkpoints.db`） | `SqliteStore` | 同上 |
| Docker / 私有云 | `PostgresSaver` | `PostgresStore`（带 pgvector） | 共享 volume 或 S3 / OSS 适配 FilesystemBackend |
| LangSmith Deployment | 平台自动 | 平台自动 | 平台自动 + `/workspace/` 关闭 |

> 切换时机：进入 staging 之前强制切到 `PostgresSaver + PostgresStore`；本地继续用 `MemorySaver` 加速开发。

### 5.4 自定义 Store（alpha 通道）

按 LangSmith 文档，自定义 `BaseStore` 可以替换默认 Postgres，例如接入 Redis / 阿里云 OSS / 向量库。需在 `langgraph.json` 中声明 `store` 指向 `async context manager`，server 负责生命周期管理。

---

## 6. 并发与限流

### 6.1 线程级

- LangGraph 自带单 thread 顺序执行（`configurable.thread_id` 互斥）。
- 跨 thread：依赖 checkpointer 后端。`PostgresSaver` 默认 `SELECT ... FOR UPDATE`，安全。
- `MemorySaver` 无并发保护，**多副本部署时禁止**。

### 6.2 LLM 调用并发

`standard_review` 已用 `asyncio.Semaphore(judge_max_workers)` 控制内容轨并发（见 `llm_judge.py`）。进一步：
- 租户级 LLM QPS 配额：用 `asyncio.Semaphore` per `(tenant_id, user_id)` 维护。
- 模型级限流：DashScope / OpenAI 平台限流时（429），按 §6.3 退避。

### 6.3 退避策略

```python
# src/standard_document_assistant/runtime/limits.py
@dataclass
class TenantQuota:
    llm_qps: int = 2
    llm_burst: int = 5
    review_concurrency: int = 2
    max_active_runs: int = 10
```

`TenantAwareMiddleware` 拦截 LLM `before_model`，按 `runtime.context.tenant` 取配额，acquire 超时则 `transient` 错。

### 6.4 速率 / 资源监控

把 `quota_acquire / quota_release` 写入 LangSmith metadata（`tenant_id`、`quota=llm_qps`）；前端通过 `message.delta` 暴露 `usage.remaining`。

---

## 7. 部署清单（`langgraph.json` + 环境）

### 7.1 `langgraph.json` 目标

```json
{
  "dependencies": ["."],
  "graphs": {
    "agent": "./agent.py:agent",
    "metadata_extraction": "./metadata_extraction_graph.py:metadata_extraction"
  },
  "env": ".env",
  "python_version": "3.12",
  "http": {
    "configurable_headers": {
      "includes": ["x-tenant-id", "x-user-id", "x-user-roles", "x-trace-id", "x-request-id"],
      "excludes": ["authorization", "x-api-key", "cookie"]
    }
  },
  "auth": {
    "path": "./auth.py:auth"
  }
}
```

### 7.2 头透传到 runtime

按 LangSmith 文档，`http.configurable_headers` 把 HTTP 头映射到 `config.configurable`，节点通过 `runtime.context` 间接获取（建议在 `auth` 中转写到 `context`）。

### 7.3 Auth 蓝图

```python
# auth.py
from langgraph_sdk import Auth

auth = Auth()

@auth.authenticate
async def authenticate(headers: dict) -> dict:
    token = headers.get("authorization", "").removeprefix("Bearer ").strip()
    claims = verify_jwt(token)  # 接入企业 IdP
    return {
        "identity": claims["sub"],
        "tenant_id": claims["tenant_id"],
        "roles": claims.get("roles", []),
        "plan": claims.get("plan", "default"),
    }

@auth.on("threads", "assistants", "runs", "store")
async def authorize(ctx, resources):
    # 仅允许用户访问自己 tenant + user 的资源
    if resources.kind == "thread":
        return _check_thread(resources)
    if resources.kind == "store":
        return _check_store_namespace(resources)
```

### 7.4 多 Assistant 配置

按 LangSmith 文档，部署后 `agent` / `metadata_extraction` 各自有 default assistant。同一 graph 还可以配置多个 assistant 变体：

```bash
# 创建企业版 assistant，使用更严的规则
curl -X POST $LANGGRAPH_URL/assistants \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "graph_id": "agent",
    "name": "标准文档-企业版",
    "config": {
      "configurable": {
        "standard_review.rules_md": "src/.../rules_strict.md",
        "standard_review.max_review_rounds": 3
      }
    }
  }'
```

后端把 `assistant_id` 自动写入 `RuntimeContext.assistant_id`（`runtime.server_info.assistant_id`），命名空间与产物目录都按 `assistant_id` 隔离。

---

## 8. Trace 与 Breadcrumbs

### 8.1 应用层 trace_id

- 入口（FastAPI / LangGraph Server）生成 `trace_id`（`tr_<uuid12>`），写到：
  - `RuntimeContext.trace_id`；
  - `config.configurable.trace_id`（兼容旧 tracing.py 读 `config["configurable"]`）；
  - LangSmith metadata（`tracing.py:build_subgraph_runnable_config`）；
  - SSE 帧 `run.started.trace_id`（前端可关联日志）。

### 8.2 Breadcrumbs 收集

`BreadcrumbsMiddleware`（AgentMiddleware / 节点）记录关键事件到 `state.breadcrumbs`：

```python
{
  "events": [
    {"ts": "...", "kind": "hitl.approval", "tool": "run_standard_review", "decision": "approve"},
    {"ts": "...", "kind": "review.widen", "from": "sectional", "to": "full_document", "round": 1},
    {"ts": "...", "kind": "review.aggregate", "issues": 17, "scopes": 13},
    {"ts": "...", "kind": "artifact.created", "tool": "run_standard_review", "ids": [...]}
  ]
}
```

`breadcrumbs` 写入 `state` 后随 `write_manifest` 一并落到产物 JSON。

### 8.3 Subgraph checkpointer 作用域

按 LangGraph 持久化文档，子图可以独立配置 checkpointer（`compile(checkpointer=...)`）。建议：
- 主图：`PostgresSaver`（跨节点、HITL）；
- `metadata_extraction` 子图：不带 checkpointer，状态通过主图透传 + Store 缓存；
- `standard_review` 子图：与主图共享 checkpointer，便于 widen 回环跨重启恢复。

```python
main_graph = builder.compile(checkpointer=postgres_saver)
metadata_subgraph = get_metadata_extraction_graph().compile()  # 无 checkpointer
review_subgraph = get_standard_review_graph().compile(checkpointer=postgres_saver)  # 共享
```

并行调用多个子图时，**给每个子图取 unique node name**（避免 namespace 冲突）：

```python
builder.add_node("metadata_extraction_v1", lambda state: invoke(metadata_subgraph, state))
builder.add_node("metadata_extraction_v2", lambda state: invoke(metadata_subgraph, state))  # 同图多实例
```

---

## 9. 与 Stream 层的衔接

- `run.started.trace_id` ← `RuntimeContext.trace_id`；
- HITL 审批流：前端 confirm → `Command(resume={"decisions": [...]})` → server 端 resume 时把 `decision` 写入 breadcrumbs；
- `subagent.completed` 时把子代理返回的 `state.breadcrumbs` 增量推给前端。

---

## 10. 验收清单

- [ ] `RuntimeContext` dataclass + `TenantContext` 定义完成；`build_context(request)` 实现。
- [ ] 主图与子图声明 `context_schema`；节点、工具、middleware 用 `runtime.context` 访问。
- [ ] `ThreadId` 解析与构造工具就绪；`build_thread_config` 解析后只把 session 段写入 LangGraph。
- [ ] `resolve_workspace_read_path / write_path` 接受 `thread` 参数，强制 `allowed_roots` 限定在用户子目录。
- [ ] uploads / outputs / artifacts 目录按 `<tenant>/<user>/<session>/<kind>/` 划分子目录。
- [ ] StoreBackend `namespace` 工厂增加 `kind` 维度，覆盖 memories / agent_assets / review_cache / run_meta。
- [ ] 切换 checkpointer / store 到 `PostgresSaver + PostgresStore`（默认本地 `MemorySaver`，部署时通过 env 切换）。
- [ ] `TenantQuota` + 退避中间件实现；429 / 5xx 自动退避。
- [ ] `langgraph.json` 包含 `http.configurable_headers`、`auth.path`；`auth.py` 接入企业 IdP。
- [ ] 多 Assistant 部署：默认 + 企业版，验证 `assistant_id` 命名空间隔离。
- [ ] `BreadcrumbsMiddleware` 写入 `state.breadcrumbs`，随 manifest 落盘。
- [ ] `trace_id` 入口生成；贯穿 SSE / LangSmith / breadcrumbs。
- [ ] 并发安全：同 thread 顺序；跨 thread Postgres 锁；LLM 限流 429 自动重试。

---

## 11. 未来可优化点

1. **多 Region / 跨云**：把 `workspace/` 切到对象存储（S3 / 阿里云 OSS），FilesystemBackend 改用 `s3fs` / `ossfs` 适配，业务路径仍用 `/workspace/...`。
2. **向量检索隔离**：用 pgvector 的 row-level security (RLS) 让每个 tenant 的 `chunk embedding` 物理隔离。
3. **Quota 与计费**：把 `TenantQuota` + 实际 LLM `usage` 推到计费网关（`message.delta.usage`），支持预付费 / 后付费。
4. **审批与合规**：HITL 决策（approve / reject / edit）写入审计日志 Store（namespace=`(tenant_id, "audit")`），对接企业 SOC2 / ISO 27001。
5. **跨 thread memory consolidation**：定期把 `state.breadcrumbs` 与短记忆聚类，写入 `memories/`，让 Deep Agents `FilesystemBackend` 持续学习用户偏好（已在 AGENTS.md 提案路径）。
6. **动态 assistant 切换**：前端选择"标准模式" / "严格模式" 时，前端调 `assistants.search` 取对应 `assistant_id`，再 `runs.create(assistant_id=...)`；后端 `runtime.server_info.assistant_id` 自动区分。
7. **运行时自检**：增加 `/health` 端点，检查 checkpointer / store / 文件系统权限 / 模型可用性；platform 部署时配 Liveness / Readiness。
8. **Graph 版本化**：`langgraph.json` + git tag 关联 `graph_id` 版本；Store namespace 追加版本号 `(assistant_id, graph_version, user_id, "memories")` 便于灰度。
9. **多模态输入**：uploads 增加图片 / 扫描 PDF；Tools 在 `runtime.context.feature_flags["ocr"]=True` 时调用 OCR 服务。
10. **可观测性增强**：除了 LangSmith，落地 OpenTelemetry trace（`opentelemetry-instrumentation-langchain`），把 `RuntimeContext` 全字段作为 baggage。
11. **Server-Sent Resume 协议升级**：当 LangGraph Server 提供原生 `since` resume 后，本项目可移除自实现 `stream_agent_sse_v3`，只保留业务事件 schema。
12. **沙箱执行**：未来 `execute` 工具如果允许运行用户代码，必须在租户级 gVisor / Firecracker 沙箱中执行。
13. **故障域隔离**：在 LangSmith Deployment 上把不同租户的 thread 路由到不同 replica 标签（用 `langgraph.json.tenant_routing` 插件）；数据库分片（按 `tenant_id` hash）。
14. **冷启动优化**：用户的第一个 run 触发 warm-up：预加载 `langchain` 嵌入、FAISS 索引、prompt 模板；把 `RuntimeContext.feature_flags.warm_start` 暴露给前端。
15. **多语言**：当前系统 prompt 中文；`RuntimeContext.feature_flags.locale` 注入 `before_agent` 中间件，动态切换 prompt 模板语言。

---

## 12. 实施路线（建议）

| 阶段 | 目标 | 关键改动 |
| --- | --- | --- |
| Phase 0（已完成） | Deep Agents 单租户开发 | `MemorySaver + InMemoryStore` + CompositeBackend（已实现） |
| Phase 1 | 多租户本地调试 | `RuntimeContext` + `ThreadId` + `user_*_dir`；本地 SQLite checkpointer |
| Phase 2 | 内部小规模多用户 | `PostgresSaver + PostgresStore`；`auth.py` + `langgraph.json` 头；TenantQuota |
| Phase 3 | LangSmith Deployment | 多 assistant；自定义 auth；pgvector；quota / 限流；监控告警 |
| Phase 4 | 大规模生产 | 跨 region；分库分表；冷启动；灰度；SLA 99.9% |
