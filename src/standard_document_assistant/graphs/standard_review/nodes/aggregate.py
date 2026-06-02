"""Aggregate subgraph: per (route, scope) scope_summary + status rollup."""

from __future__ import annotations

from collections import Counter
from typing import Any

from standard_document_assistant.graphs.standard_review.state import StandardReviewState
from standard_document_assistant.pathing import utc_now_iso


def aggregate(state: StandardReviewState) -> dict[str, Any]:
    issues = state.get("issues") or []
    statuses = Counter(item.get("status", "") for item in issues)
    severities = Counter(item.get("severity", "") for item in issues)
    tracks = Counter(item.get("audit_track", "") for item in issues)
    scope_buckets: dict[str, dict[str, Any]] = {}
    for issue in issues:
        scope = issue.get("scope") or "full_document"
        track = issue.get("audit_track") or "content_llm"
        key = f"{track}::{scope}"
        bucket = scope_buckets.setdefault(
            key,
            {
                "track": track,
                "scope": scope,
                "total": 0,
                "fail": 0,
                "warn": 0,
                "pass": 0,
                "insufficient": 0,
                "issues": [],
            },
        )
        bucket["total"] += 1
        status = issue.get("status", "")
        if status == "pass":
            bucket["pass"] += 1
        elif status in {"warn"}:
            bucket["warn"] += 1
        elif status == "fail":
            bucket["fail"] += 1
        elif status == "insufficient_context":
            bucket["insufficient"] += 1
        bucket["issues"].append(issue.get("issue_id", ""))

    summary = {
        "total_issues": len(issues),
        "failed": statuses.get("fail", 0),
        "warn": statuses.get("warn", 0),
        "insufficient_context": statuses.get("insufficient_context", 0),
        "by_severity": dict(severities),
        "by_track": dict(tracks),
        "by_scope": {key: {k: v for k, v in bucket.items() if k != "issues"} for key, bucket in scope_buckets.items()},
    }
    final_status = "success" if not state.get("errors") else "failed"
    return {
        "scope_summary": scope_buckets,
        "aggregate_summary": summary,
        "status": final_status,
        "final_status": final_status,
        "trace_events": [_event(state, "aggregate", "success", {"buckets": len(scope_buckets)})],
    }


def _event(state: StandardReviewState, node: str, status: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
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
    return payload
