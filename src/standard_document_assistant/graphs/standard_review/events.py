"""Standard review 流式事件助手。

设计目标（2026-06-03 rev. 4）
==============================

1. **统一命名空间**：把 standard_review 节点原 ``state["trace_events"]`` 中的
   ``(node, status)`` 二元组映射到 ``review.<stage>.<state>`` 命名空间，
   与 MinerU 的 ``mineru.*``、langextract 的 ``meta.*`` 共同形成
   ``<domain>.<stage>`` 统一规范，前端通过 ``stream_mode="custom"`` 消费
   （参考 [stream_sse_design.md](file:///d:/deep-agents/design_docs/stream_sse_design.md)）。

2. **统一 payload schema**：流式事件统一通过 ``streaming.emit_stream_event``
   构造，含 ``type / trace_id / component / created_at`` 公共字段，与
   MinerU / langextract 工具层共享一份最小公共 schema（2026-06-03 rev. 4）。

3. **Dual-channel 输出**：函数 :func:`emit_event` 同时：
   - 通过 ``streaming.emit_stream_event`` 推送 ``review.*`` 事件（统一 schema）；
   - 返回与旧 ``_event`` 完全同构的 dict（含 ``node / event / status`` 字段）
     便于继续累加到 ``state["trace_events"]``，**不破坏** reviewer subagent、
     report.json、trace.json 的现有消费者。

4. **失败容忍**：若图外调用（单测 / Tool 直调），``get_stream_writer``
   可能抛 ``RuntimeError``，函数静默忽略，只返回 dict；不会影响主流程。

5. **权威依据**：依据 [docs-langchain Stream writer](https://docs.langchain.com/oss/python/langchain/tools#stream-writer)
   + ``langgraph-fundamentals`` skill 中的 ``get_stream_writer`` 模式；
   节点签名升级为 ``(state, runtime=None)`` 兼容 LangGraph 1.2+ 节点签名
   约定（深 agents-core skill — 节点签名）。
"""

from __future__ import annotations

from typing import Any

from standard_document_assistant.graphs.standard_review.state import StandardReviewState
from standard_document_assistant.pathing import utc_now_iso
from standard_document_assistant.streaming import emit_stream_event


# 命名空间映射：``(legacy_node, legacy_status)`` -> ``review.<stage>`` 类型。
# - 把长 node 名压缩为短 stage 名（如 ``judge_rules`` -> ``judge``）；
# - status 直接拼到末尾，统一小写；
# - 新增事件请按此表追加，不要在节点里硬编码 type。
LEGACY_EVENT_TO_REVIEW_TYPE: dict[tuple[str, str], str] = {
    # ingest
    ("ingest", "started"): "review.ingest.started",
    ("ingest", "success"): "review.ingest.success",
    ("ingest", "failed"): "review.ingest.failed",
    ("ingest", "format_only"): "review.ingest.format_only",
    # retrieve_rules
    ("retrieve_rules", "success"): "review.retrieve.success",
    ("retrieve_rules", "failed"): "review.retrieve.failed",
    # judge_rules
    ("judge_rules", "skipped"): "review.judge.skipped",
    ("judge_rules", "success"): "review.judge.success",
    ("judge_rules", "failed"): "review.judge.failed",
    ("judge_rules", "empty"): "review.judge.empty",
    ("judge_rules", "deterministic"): "review.judge.deterministic",
    ("judge_rules", "coverage_check"): "review.coverage.completed",
    # quality_gate
    ("quality_gate", "widen"): "review.quality_gate.widen",
    ("quality_gate", "ok"): "review.quality_gate.ok",
    # widen + reload
    ("widen_review_scope", "success"): "review.widen.success",
    ("reload_review_rules", "success"): "review.widen.rules_reloaded",
    ("reload_review_rules", "failed"): "review.widen.failed",
    # format_review
    ("format_review", "success"): "review.format.success",
    ("format_review", "skipped"): "review.format.skipped",
    ("format_review", "failed"): "review.format.failed",
    # aggregate / report / manifest
    ("aggregate", "success"): "review.aggregate.success",
    ("write_outputs", "success"): "review.report.written",
    ("write_manifest", "success"): "review.manifest.written",
}


def review_event_type(node: str, status: str) -> str:
    """根据 (node, status) 计算 ``review.*`` 事件类型。"""

    return LEGACY_EVENT_TO_REVIEW_TYPE.get((node, status), f"review.{node}.{status}")


def _safe_stream_writer() -> Any | None:
    """获取 stream writer；图外调用（单测 / Tool 直调）时返回 None。

    委托给 :func:`standard_document_assistant.streaming.safe_stream_writer`，
    保持与 MinerU / langextract 工具层共用同一份失败容忍逻辑。
    """

    from standard_document_assistant.streaming import safe_stream_writer

    return safe_stream_writer()


def emit_event(
    state: StandardReviewState,
    node: str,
    status: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """统一事件出口：既写 state["trace_events"]，也通过 get_stream_writer 推送。

    Parameters
    ----------
    state : StandardReviewState
        当前 graph state（用于透传 trace_id / job_id）。
    node : str
        节点名（与旧 ``_event`` 一致；如 ``judge_rules`` / ``quality_gate``）。
    status : str
        节点状态（与旧 ``_event`` 一致；如 ``success`` / ``failed`` / ``widen``）。
    extra : dict | None
        附加字段（如 ``plans``、``issues``、``strategies`` 等计数）。

    Returns
    -------
    dict
        与旧 ``_event`` 同构的 dict，可继续 ``state["trace_events"].append(...)``。
    """

    payload: dict[str, Any] = {
        "trace_id": state.get("trace_id", ""),
        "job_id": state.get("job_id", ""),
        "component": "standard_review_graph",
        "node": node,
        "event": node,
        "status": status,
        "created_at": utc_now_iso(),
    }
    if extra:
        payload.update(extra)

    # 流式推送（双通道），但不阻塞 state 累加。
    # 使用统一 payload schema（rev. 4）：type / trace_id / component / created_at 公共字段。
    emit_stream_event(
        review_event_type(node, status),
        trace_id=str(state.get("trace_id") or ""),
        component="standard_review_graph",
        job_id=str(state.get("job_id") or ""),
        node=node,
        event=node,
        status=status,
        **(extra or {}),
    )
    return payload


def emit_event_with_type(
    state: StandardReviewState,
    type_name: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """直接指定 ``review.*`` 类型的事件出口（用于表外特殊事件）。"""

    payload: dict[str, Any] = {
        "trace_id": state.get("trace_id", ""),
        "job_id": state.get("job_id", ""),
        "component": "standard_review_graph",
        "event": type_name,
        "created_at": utc_now_iso(),
    }
    if extra:
        payload.update(extra)
    # 流式推送（统一 schema）
    emit_stream_event(
        type_name,
        trace_id=str(state.get("trace_id") or ""),
        component="standard_review_graph",
        job_id=str(state.get("job_id") or ""),
        event=type_name,
        **(extra or {}),
    )
    return payload
