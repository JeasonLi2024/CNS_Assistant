# 流式输出与 SSE 适配设计

> 适用框架：Deep Agents v0.6（基于 LangGraph / LangChain `create_agent` 风格）+ LangGraph Server / LangSmith Deployment。
> 适用代码：`d:\deep-agents\src\standard_document_assistant\streaming.py`、`graphs/metadata_extraction/`、`graphs/standard_review/`、`agent.py`、`langgraph.json`。
> 设计目标：
> 1. 业务侧（标准文档助理）通过统一 SSE 协议把 LLM token、节点进度、工具结果、subagent 生命周期、HITL 审批、断点恢复推给前端；
> 2. 充分利用 Deep Agents v0.6 的 typed-projection Event Streaming API（`stream.subagents` / `stream.messages` / `stream.tool_calls` / `stream.values`），避免在 `stream_mode` tuple 上做分支判断；
> 3. 保持与 LangGraph Server Protocol v2 SSE 一致，使本地 `langgraph dev` 与未来 LangSmith Deployment 共享同一前端协议层。

---

## 1. 现状与缺口

| 关注点 | 现状 | 缺口 |
| --- | --- | --- |
| Stream API | `streaming.py:stream_agent_sse` 使用 `agent.astream(stream_mode=["updates", "values"])`，手工分支 `__interrupt__` / `todos` / `messages`。 | 未启用 Deep Agents v0.6 typed-projection API；不能直接拿到 subagent 生命周期、tool call delta、namespace path。 |
| 节点进度 | 子图节点仅靠 `trace_events` 写入 state，**不会** 推送给流。 | 节点内进度对前端不可见；长审核过程（13 scope × 多策略）无可见信号。 |
| LLM token | 没有 token 级别流；`message.delta` 是把整条 `update["messages"]` 序列化为字符串。 | 前端看到的 `message.delta` 是整条 message，不是真正的 token。 |
| Subagent 隔离 | 当前 subagent 通过 `task(...)` 调用；流上 `path` / `name` / `status` 字段无统一标识。 | 无法在 UI 上把 "reviewer" / "extractor" / "parser" 子代理的输出分组呈现。 |
| Resume / 重连 | 无 `since` 机制；HTTP 断开即丢。 | 前端刷新或网络抖动后无法续接；HITL 审批恢复路径不可见。 |
| Heartbeat | 无。 | 反向代理 / 浏览器 / LangGraph Studio 可能超时静默关闭。 |
| 错误重试 | `run.failed` 直接发出；不可重试。 | 长时审核中网络抖动无任何恢复策略。 |
| 协议语义 | 自定义事件名 `run.started / plan.updated / message.delta / artifact.created / approval.required / run.completed / run.failed`。 | 缺少 `chunk.delta`（token）、`subagent.started/completed/failed/interrupted`、`tool.delta`（token 级工具输出）、`thread.heartbeat`、`node.entered/node.exited`、`run.snapshot`（用于断点续传）等语义。 |

---

## 2. 设计总览

### 2.1 三层流式栈

```
┌────────────────────────────────────────────────────────────────┐
│  Frontend (Browser / Agent Chat / LangGraph Studio)            │
│  - EventSource 客户端（带 since seq 重连 / fallback POST）     │
└────────────────────────────────────────────────────────────────┘
                        ▲  text/event-stream
                        │  + seq 序号
                        ▼
┌────────────────────────────────────────────────────────────────┐
│  Server 层  src/standard_document_assistant/streaming.py       │
│  - Deep Agents event_stream(version="v3")                      │
│  - typed-projection 适配：subagents / messages / tool_calls    │
│  - 业务事件协议（run.* / plan.* / scope.* / node.* / tool.*）  │
│  - heartbeat / seq / reconnection                              │
└────────────────────────────────────────────────────────────────┘
                        ▲
                        │  In-process stream
                        ▼
┌────────────────────────────────────────────────────────────────┐
│  Graph 层                                                     │
│  - Main Deep Agent (create_deep_agent)                         │
│  - Subagents: parser / extractor / reviewer / research / writer│
│  - Subgraphs: metadata_extraction / standard_review            │
│  - 节点用 get_stream_writer() 推送 progress / scope / tool     │
└────────────────────────────────────────────────────────────────┘
```

### 2.2 三类 Stream 源

| 源 | 通道 | 用途 | 推荐 API |
| --- | --- | --- | --- |
| Coordinator 顶层 | `messages` / `tool_calls` / `values` | 主代理与用户的对话、最终 `AgentResult`、HITL `approval.required` | `stream.messages`、`stream.tool_calls`、`stream.values`（v3） |
| Subagent 委派 | `subagents.<name>.messages/tool_calls/output` | reviewer/extractor/parser 内部进度 | `stream.subagents`（v3） |
| Subgraph 内部节点 | `custom`（`get_stream_writer`） | scope 进度、QA 回环、widen、format、aggregate | `get_stream_writer()` + `stream_mode=["custom", "updates"]` |

---

## 3. 业务事件协议（前端契约）

> SSE 帧形如 `event: <name>\nid: <seq>\ndata: <json>\n\n`。
> `id` 单调递增，`seq` 用于 `since` 续传；Protocol v2 的端点是 POST，浏览器侧的 `EventSource` 自动 `Last-Event-ID` 不可用，**前端必须自行缓存最后 `seq`**（可写入 `sessionStorage`）并在重连时随请求体带回。

### 3.1 事件清单

| 事件 | 数据 | 触发方 | 含义 |
| --- | --- | --- | --- |
| `run.started` | `{run_id, thread_id, assistant_id, graph_id, user_id, started_at}` | server | run 已注册，订阅建立 |
| `thread.heartbeat` | `{run_id, thread_id, seq, ts}` | server | 15s 静默期触发；保活 + 时序参考 |
| `plan.updated` | `{run_id, todos: [...]}` | coordinator | Deep Agents `TodoListMiddleware` 更新 |
| `message.delta` | `{role, content_delta, namespace}` | coordinator / subagent | LLM 文本 token；`namespace` 标识来源（`""` / `subagents:reviewer` 等） |
| `tool.started` | `{tool_name, tool_call_id, input_preview, namespace}` | subagent | 工具调用开始 |
| `tool.delta` | `{tool_name, tool_call_id, output_delta}` | subagent | 工具输出 token（如未来工具用流式 LLM） |
| `tool.completed` | `{tool_name, tool_call_id, output_summary, artifact_ids?, duration_ms}` | subagent | 工具完成 + 自动注册产物 |
| `node.entered` / `node.exited` | `{node, namespace, round?}` | subgraph | 子图节点生命周期（仅在 `stream_mode="debug"` 或包装模式下发出） |
| `scope.progress` | `{scope, strategy, plan_idx, total, round}` | subgraph | `judge_rules` 推进一条 plan 时推送 |
| `scope.summary` | `{scope, issues, severity_counts}` | subgraph | `aggregate` 后按 scope 输出 |
| `widen.detected` | `{scope, round, max_round}` | subgraph | `quality_gate` 决定扩大范围 |
| `widen.applied` | `{new_partial_mode, round}` | subgraph | `widen_review_scope` 写入状态 |
| `format.completed` | `{findings, severity_counts}` | subgraph | `format_review` 完成 |
| `subagent.started` / `subagent.completed` / `subagent.failed` / `subagent.interrupted` | `{name, path, status, output?}` | coordinator | Deep Agents `stream.subagents` |
| `artifact.created` | `{artifact_id, tool, virtual_path, sha256, size_bytes}` | server | 业务工具产物注册 |
| `approval.required` | `{tool_name, args_preview, interrupt_id}` | coordinator | HITL 中断；前端展示 confirm 弹窗 |
| `run.snapshot` | `{checkpoint_id, next_node, state_keys}` | server | 阶段性 checkpoint；用于断点续传 |
| `run.completed` | `{run_id, final_output, total_tokens?, duration_ms}` | server | run 成功结束 |
| `run.failed` | `{error, code, recoverable, retry_after_s?}` | server | run 失败；`recoverable=true` 提示前端可重试 |

### 3.2 错误分类

| code | 含义 | 前端动作 |
| --- | --- | --- |
| `transient` | 网络/限流，1xx 类 | 退避重试 + `retry_after_s` |
| `interrupted` | HITL 等待审批 | 拉 `approval.required` 详情并展示 |
| `validation` | 入参非法 | 报错并禁止重试 |
| `internal` | 系统异常 | 提示刷新 + 联系管理员；可选 `run.snapshot` 回放 |

---

## 4. Server 层 SSE 适配

### 4.1 主流程（`streaming.py:stream_agent_sse_v3`）

```python
async def stream_agent_sse_v3(
    agent: Any,
    *,
    thread_id: str,
    input_payload: dict[str, Any],
    runtime_context: dict[str, Any] | None = None,
    heartbeat_interval_s: float = 15.0,
    since_seq: int = 0,
):
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    seq = since_seq
    hb_task: asyncio.Task | None = None

    async def _hb_loop():
        nonlocal seq
        while True:
            await asyncio.sleep(heartbeat_interval_s)
            seq += 1
            yield _frame("thread.heartbeat", seq, {"run_id": run_id, "thread_id": thread_id, "seq": seq, "ts": utc_now_iso()})

    config = build_thread_config(thread_id)
    if runtime_context:
        config["context"] = runtime_context  # 透传 user/tenant 到 Runtime

    yield _frame("run.started", seq := seq + 1, _run_started(run_id, thread_id, runtime_context))

    try:
        # v3 typed-projection 关键 API
        stream = agent.astream_events(input_payload, config=config, version="v3")

        # 1) Subagent 生命周期：coordinator 委派到 reviewer/extractor 时立刻可见
        async def consume_subagents():
            nonlocal seq
            async for sub in stream.subagents:
                seq += 1
                yield _frame("subagent.started", seq, _sub_payload(sub, "started"))
                try:
                    final = await sub.output
                    seq += 1
                    yield _frame("subagent.completed", seq, _sub_payload(sub, "completed", final))
                except Exception as exc:
                    seq += 1
                    yield _frame("subagent.failed", seq, _sub_payload(sub, "failed", exc))

        # 2) Coordinator / Subagent 的 LLM token
        async def consume_messages():
            nonlocal seq
            async for msg in stream.messages:
                seq += 1
                yield _frame(
                    "message.delta",
                    seq,
                    {
                        "role": msg.role,
                        "content_delta": await msg.text,
                        "namespace": msg.namespace or "",
                    },
                )

        # 3) Tool calls（节点进度、产物）
        async def consume_tools():
            nonlocal seq
            async for call in stream.tool_calls:
                seq += 1
                yield _frame("tool.started", seq, _tool_payload(call, "started"))
                async for delta in call.output_deltas:
                    seq += 1
                    yield _frame("tool.delta", seq, _tool_delta_payload(call, delta))
                if call.error:
                    seq += 1
                    yield _frame("tool.failed", seq, _tool_payload(call, "failed", call.error))
                else:
                    seq += 1
                    yield _frame("tool.completed", seq, _tool_payload(call, "completed", call.output))

        # 4) Custom：get_stream_writer 推送的 scope / widen / format / aggregate
        #    使用 stream_mode=["custom"] 通过原生 events 流消费
        async def consume_custom():
            nonlocal seq
            # 在 astream_events 中 custom 通道以 "events" 事件出现；
            # 或直接对子图用 .astream(stream_mode="custom") 收集
            async for ev in stream:
                if ev.get("event") != "on_custom_event":
                    continue
                seq += 1
                payload = ev["data"]
                yield _frame(payload["type"], seq, payload)

        # 并发消费，模拟 v3.interleave 行为
        await asyncio.gather(
            _drain(consume_subagents()),
            _drain(consume_messages()),
            _drain(consume_tools()),
            _drain(consume_custom()),
            _drain(_hb_loop()),
        )

        yield _frame("run.completed", seq := seq + 1, {"run_id": run_id, "thread_id": thread_id})

    except asyncio.CancelledError:
        yield _frame("run.failed", seq := seq + 1, {"code": "transient", "recoverable": True, "retry_after_s": 2})
        raise
    except Exception as exc:
        yield _frame("run.failed", seq := seq + 1, _format_error(exc))
```

要点：
1. **typed-projection 优先**：用 `stream.subagents` / `stream.messages` / `stream.tool_calls` 直接拿到字段化结果，而不是在 `(mode, chunk)` tuple 上做 `isinstance`。
2. **顺序保证**：用 `seq` 单调自增，前端按 `seq` 排序，不依赖到达时间。
3. **heartbeat** 与正常事件共用 `seq`；前端若 30s 未收到 `thread.heartbeat` 应主动重连。
4. **`asyncio.gather` 模拟 `interleave`**：v3 的 `stream.interleave(...)` 仅同步版；异步下用 `gather` 即可。
5. **`since` 重连**：保留 `since_seq`；下次调用用相同 `thread_id` 重新订阅，server 端靠 LangGraph checkpointer 重放 checkpoint 之间的 delta（`get_state_history` + `stream_mode="values"`）。
6. **取消语义**：客户端断开触发 `CancelledError`；后端把 `run.failed` 标记 `recoverable=true`，允许前端以 `since=last_seq` 续。

### 4.2 协议帧编码

```python
def _frame(event: str, seq: int, data: dict[str, Any]) -> str:
    # Protocol v2 风格：event / id / data 三段
    return f"event: {event}\nid: {seq}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
```

- `id: <seq>` 让浏览器侧在 fallback 到 `EventSource` 时仍可读到最后序号（`EventSource.lastEventId`）。
- 标准 SSE 默认 `data:` 是单行；多行需每行前缀 `data: `。本项目所有 payload 走 `json.dumps` 单行即可。

### 4.3 与 LangGraph Server Protocol v2 的关系

- LangGraph Server 自带 `POST /threads/{id}/runs/stream` 端点（`text/event-stream`），与本设计兼容：事件名 `messages` / `updates` / `values` / `events` / `end` / `error`，POST-only、客户端用 `since` resume。
- 本项目的 `streaming.py` 提供与 LangGraph Server 兼容的事件名集合（`message.delta` ↔ `messages`、`subagent.completed` ↔ `values` 末段），便于：
  - 本地 `langgraph dev`：直接走 server 端 `runs.stream`；
  - 自行部署 FastAPI + `agent.astream_events(version="v3")`：复用 `stream_agent_sse_v3`。
- 后续可参考 [Protocol v2 Event Stream (SSE)](https://docs.langchain.com/langsmith/agent-server-api/streaming/protocol-v2-event-stream-sse) 在 server 端实现 `ProtocolEventStreamRequest`（含 channel / namespace filter）做 server-side fan-out。

---

## 5. 子图流式协议

### 5.1 metadata_extraction 子图

节点拓扑（见 [`metadata_extraction/graph.py`](file:///d:/deep-agents/src/standard_document_assistant/graphs/metadata_extraction/graph.py#L1-L41)）：

```
load_markdown → slice_scope → run_langextract → aggregate_fields
              → validate_schema → persist_output → write_manifest
```

#### 节点内 `get_stream_writer` 推送

| 节点 | 自定义事件 | payload |
| --- | --- | --- |
| `load_markdown` | `meta.loaded` | `{source_virtual_path, chars, sha256}` |
| `slice_scope` | `meta.scoped` | `{mode, scoped_chars, truncated: bool}` |
| `run_langextract` | `meta.extraction.start` / `meta.extraction.delta` / `meta.extraction.end` | `{pass_idx, model, delta?}`；未来 Langextract 自身若流式则推 token |
| `aggregate_fields` | `meta.aggregated` | `{fields_filled, missing_keys, quality_warnings_count}` |
| `validate_schema` | `meta.validated` | `{valid, errors_count}` |
| `persist_output` | `artifact.created` × 3 | `{metadata_json, annotated, normalized}`（由 `artifacts.register_from_tool_result` 触发） |
| `write_manifest` | `meta.completed` | `{manifest_virtual_path, total_duration_ms}` |

> 关键点：所有 `get_stream_writer` 推数据时**同步把 namespace 加上**（`"subgraphs:metadata_extraction"`），便于前端在子图与 subagent 之间分别呈现。

#### token 级流（langextract 现状）

- 当前 `langextract_runner.run_extraction` 是同步批量调用 `qwen3.5-flash`。
- 未来若切换为 OpenAI-compatible streaming 客户端（`ChatOpenAI(..., streaming=True).astream(...)`），可在 `run_langextract` 中：
  1. 用 `astream` 拿到 token；
  2. 通过 `get_stream_writer()` 推送 `meta.extraction.delta`；
  3. 最终用 `ai messages` 累加 + `aggregate_fields` 二次加工。
- 离线 fallback（无 API key）用 `FakeListChatModel` 切到 `stream_mode="non"` 不发 token delta。

### 5.2 standard_review 子图

节点拓扑（见 [`standard_review/graph.py`](file:///d:/deep-agents/src/standard_document_assistant/graphs/standard_review/graph.py#L1-L67)）：

```
START → ingest → retrieve_rules → judge_rules → quality_gate
                                              ↙      ↓      ↘
                                widen_review_scope  format_review  aggregate
                                       ↓                              ↓
                                reload_review_rules                write_outputs
                                       ↓                              ↓
                                  (loop to judge)              write_manifest → END
```

#### 节点内 `get_stream_writer` 推送

| 节点 | 自定义事件 | payload |
| --- | --- | --- |
| `ingest` | `review.ingested` | `{virtual_md_path, scopes_count, total_chars}` |
| `retrieve_rules` | `review.rules_loaded` | `{index_kind, rules_count, faiss_used: bool}` |
| `judge_rules` | `review.judge.start` / `review.judge.scope` / `review.judge.end` | `{round, total_plans, scope, strategy, plan_idx, total, issues_count}`；每条 `JudgePlan` 跑完推 `scope.progress` |
| `quality_gate` | `review.gate` | `{action: widen|ok, round, max_round, insufficient_scopes}` |
| `widen_review_scope` | `widen.applied` | `{new_partial_mode, round, scopes}` |
| `reload_review_rules` | `review.rules_loaded` | `{round, full_doc_rules_count, section_rules_count}` |
| `format_review` | `format.completed` | `{findings_count, severity_counts}` |
| `aggregate` | `scope.summary` × N | `{scope, issues_count, severity_counts, audit_track}` |
| `write_outputs` | `artifact.created` × 4 | `{review_report, review_result, review_trace, review_manifest}` |
| `write_manifest` | `review.completed` | `{manifest_virtual_path, total_duration_ms, rounds}` |

#### 回环可见性

`quality_gate → widen_review_scope → reload_review_rules → judge_rules` 是多次回环（`review_round ∈ [1, max_review_rounds]`）。前端要看到第几轮：

```python
# judge_rules 节选
from langgraph.config import get_stream_writer

def judge_rules(state: StandardReviewState) -> dict[str, Any]:
    writer = get_stream_writer()
    writer({"type": "review.judge.start", "round": new_round, "total_plans": len(plans)})
    for i, plan in enumerate(plans, 1):
        outcome = judge.run(plan)
        writer({
            "type": "review.judge.scope",
            "round": new_round,
            "scope": plan.scope,
            "strategy": plan.strategy,
            "plan_idx": i,
            "total": len(plans),
            "issues_count": 1 if outcome.issue else 0,
        })
    writer({"type": "review.judge.end", "round": new_round, "issues": len(issues)})
    return {...}
```

> `langgraph.config.get_stream_writer` 是官方推荐 API；Python < 3.11 不能用时改 `writer` 形参手动传入（标准库注释）。

#### 工具内部流

`run_standard_review` / `run_format_source_review` 本身是 `StructuredTool`，被 reviewer subagent 调用。流上需要：
- server 层 `stream.tool_calls` 自动捕获 `tool.started` / `tool.completed` / `tool.delta`（若工具内用 `get_stream_writer`）；
- 工具完成后 `artifacts.register_from_tool_result` 触发 `artifact.created` × 4。

---

## 6. 节点进度推送规范（统一约定）

为避免散落 `get_stream_writer` 调用不一致，封装 `streaming/progress.py`：

```python
# src/standard_document_assistant/streaming/progress.py
from langgraph.config import get_stream_writer
from typing import Any

class ProgressBus:
    """统一的节点进度推送总线。"""

    @staticmethod
    def emit(event_type: str, **fields: Any) -> None:
        writer = get_stream_writer()
        payload = {"type": event_type, "ts": utc_now_iso(), **fields}
        writer(payload)

    @classmethod
    def review_judge_start(cls, *, round: int, total: int) -> None:
        cls.emit("review.judge.start", round=round, total_plans=total)

    @classmethod
    def review_judge_scope(cls, *, round: int, scope: str, strategy: str, plan_idx: int, total: int, issues_count: int) -> None:
        cls.emit("review.judge.scope", round=round, scope=scope, strategy=strategy,
                 plan_idx=plan_idx, total=total, issues_count=issues_count)

    # ... 其余事件 ...
```

节点内只 `from standard_document_assistant.streaming.progress import ProgressBus`，减少拼写错误与事件漂移。

---

## 7. 错误、重试、断点续传

### 7.1 错误分类与重试

| 错误 | code | 重试策略 |
| --- | --- | --- |
| LLM 限流 (`429`) | `transient` | `retry_after_s` 按 server 返回；最多 3 次指数退避 |
| LLM 超时 | `transient` | 2 / 4 / 8s 退避；同步 fallback 到 `FakeListChatModel`（若配置允许） |
| MinerU 5xx | `transient` | 1 / 2 / 4s，最多 2 次 |
| MinerU 4xx（参数错） | `validation` | 不可重试 |
| Langextract 解析异常 | `internal` | 标记 `quality_warning` 继续；不阻断 |
| LangGraph interrupt | `interrupted` | 等待 `Command(resume=...)` |
| 磁盘满 / 权限 | `internal` | 不可重试 |
| 工具超时（自定义） | `transient` | 退避 5s 一次 |

### 7.2 断点续传（client-side）

```ts
// 伪代码：前端 useStream 钩子
const lastSeq = Number(sessionStorage.getItem(`sse_seq:${threadId}`) || 0);
const url = `/api/threads/${threadId}/runs/stream?since=${lastSeq}`;
const es = new EventSource(url);  // 浏览器 fallback
// 后端用 POST 端点 + since body 时走 fetch + ReadableStream

es.addEventListener('run.started', e => { lastSeq = e.lastEventId; ... });
es.addEventListener('message.delta', e => { lastSeq = e.lastEventId; append(e.data); });
// 30s 无 heartbeat → 关闭并重连
```

> 协议层支持 `since` 是关键。前端不依赖 `Last-Event-ID` 浏览器自动行为，统一用 body / query 传。

### 7.3 服务端断点续传实现

```python
async def stream_agent_sse_v3(..., since_seq: int = 0):
    # 1) 读取历史 checkpoint
    config = build_thread_config(thread_id)
    history = agent.get_state_history(config)
    if since_seq > 0 and history:
        # 用 checkpointer 重放 since 之后的事件
        async for ev in replay_checkpoints(history, since_seq):
            yield _frame(ev["event"], ev["seq"], ev["data"])
    # 2) 继续实时流
    async for ev in _live_stream(...):
        yield ev
```

LangGraph checkpointer（`MemorySaver` / `SqliteSaver` / `PostgresSaver`）是天然的事件源；按 `seq` 截断即可。

---

## 8. 与 LangGraph Server 协同

| 部署形态 | 流式入口 | 说明 |
| --- | --- | --- |
| 本地 `langgraph dev` | `POST /threads/{id}/runs/stream` | 官方 SSE 端点（`version="v2"`），自动 heartbeat、`since` resume。**推荐**作为本地联调。 |
| LangSmith Deployment | `POST /threads/{id}/runs/stream` | 同上，多副本下 `assistant_id` / `user` 由平台注入。 |
| 自行 FastAPI 部署 | `agent.astream_events(version="v3")` + `stream_agent_sse_v3` | 完全控制事件 schema；需要自行实现 heartbeat、`since` resume、产物下载。 |

> 业务事件 schema 与 LangGraph Server 原生 schema 解耦。可以在 server 端做适配层：
> 1. 收到原生 `messages/2` → 翻译为 `message.delta`；
> 2. 收到 `updates` 中的 `__interrupt__` → 翻译为 `approval.required`；
> 3. 收到 `events` 中 `on_custom_event` → 透传 `type` 作为 `event` 名。

---

## 9. 未来可优化点

1. **统一事件总线 v.s. 三种 stream 模式**：v0.6 之后，typed-projection 已统一；后续可下线 `stream_mode="updates"` 兼容层。
2. **多协议 Adapter**：把 `stream_agent_sse_v3` 与 LangGraph Server v2 协议做共享中间层，业务事件同时输出 SSE + WebSocket（WebSocket 用于双向 resume / cancel）。
3. **Stream replay 持久化**：把 stream 事件写入 LangSmith / 自建 Store（key=`(thread_id, run_id, seq)`），便于用户回放；与 checkpointer 解耦。
4. **LLM token 计费与配额**：在 `tool.delta` / `message.delta` 中累计 `usage`，结合多用户限流（见 [multi_user_runtime_design.md](file:///d:/deep-agents/design_docs/multi_user_runtime_design.md)）。
5. **客户端 SDK**：基于 `useStream` React Hook 抽象出 `useStandardDocStream` 提供 `plan / scope / subagent` 三类高阶信号。
6. **Stream 压缩**：长 PDF 标准文档审核下，token 量大；可使用 SSE `retry`/`comment` + 客户端 zstd 压缩。
7. **可观测性增强**：把 `seq` / `node` / `namespace` 写入 LangSmith trace metadata；在 Studio 中可按 namespace 折叠。
8. **多模态流**：MinerU 已输出图片与表格 JSON，未来工具内部可把图片描述通过 `tool.delta`（base64 + delta 协议）推给前端。
9. **协议版本协商**：用 `Accept: text/event-stream; version=1` 协商协议版本，便于灰度升级。
10. **断点恢复语义的统一**：HITL interrupt 与网络断开 reconnect 应共享同一恢复流程（`since` + `Command(resume=...)`）。

---

## 10. 验收清单

- [ ] `streaming.py` 升级到 `version="v3"` typed-projection，支持 `subagents` / `messages` / `tool_calls` 三个独立通道。
- [ ] 新增 `streaming/progress.py` 总线，所有节点通过 `ProgressBus` 推送。
- [ ] `stream_agent_sse_v3` 帧格式 `event / id / data`，单调 `seq`，`thread.heartbeat` 周期 15s。
- [ ] `metadata_extraction` / `standard_review` 各节点按 §5 表格发出对应事件。
- [ ] `run.snapshot` 在 `quality_gate` / `aggregate` / `write_manifest` 三处发出 checkpoint id。
- [ ] `approval.required` 与 `Command(resume=...)` 形成闭环（前端 confirm → 后端 resume → 流恢复）。
- [ ] 客户端重连测试：人为断开 30s 后用 `since=last_seq` 续接，不丢业务事件。
- [ ] LangGraph Server v2 协议适配层就绪，本地 `langgraph dev` 可见标准文档审核全流程。
