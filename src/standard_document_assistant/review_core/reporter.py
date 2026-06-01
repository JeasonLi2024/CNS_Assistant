"""Report generation helpers for standard review."""

from __future__ import annotations

from typing import Any


def render_markdown_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    issues = payload.get("issues") or []
    warnings = payload.get("warnings") or []
    lines = [
        "# 标准审核报告",
        "",
        "## 摘要",
        "",
        f"- 审核状态：{payload.get('status', 'success')}",
        f"- 问题总数：{summary.get('total_issues', 0)}",
        f"- 不通过：{summary.get('failed', 0)}",
        f"- 警告：{summary.get('warn', 0)}",
        f"- 依据不足：{summary.get('insufficient_context', 0)}",
        "",
        "## 审核发现",
        "",
    ]
    if not issues:
        lines.append("未发现结构化问题。")
    for item in issues:
        lines.extend(
            [
                f"### {item.get('issue_id', '')} {item.get('rule_name', '')}".strip(),
                "",
                f"- 状态：{item.get('status', '')}",
                f"- 严重级别：{item.get('severity', '')}",
                f"- 范围：{item.get('scope', '')}",
                f"- 审核轨道：{item.get('audit_track', '')}",
                f"- 规则：{item.get('rule_id', '')}（{item.get('source_ref', '')}）",
                f"- 期望：{item.get('expected', '')}",
                f"- 实际：{item.get('actual', '')}",
                f"- 证据：{item.get('evidence_text', '') or '依据不足'}",
                f"- 建议：{item.get('suggestion', '')}",
                "",
            ]
        )
    if warnings:
        lines.extend(["## 警告", ""])
        lines.extend(f"- {warning}" for warning in warnings)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"

