"""Rule loading utilities for the standard review P0 graph."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from standard_document_assistant.config import load_config
from standard_document_assistant.constants import PROJECT_ROOT


DEFAULT_RULES: list[dict[str, Any]] = [
    {
        "rule_id": "SR-P0-001",
        "rule_name": "范围章节完整性",
        "scope": "scope",
        "severity": "major",
        "source_ref": "built-in:SR-P0-001",
        "text": "标准应包含范围章节，并说明标准适用对象和边界。",
    },
    {
        "rule_id": "SR-P0-002",
        "rule_name": "规范性引用文件章节完整性",
        "scope": "normative_references",
        "severity": "major",
        "source_ref": "built-in:SR-P0-002",
        "text": "标准应明确规范性引用文件；无引用时应说明无规范性引用文件。",
    },
    {
        "rule_id": "SR-P0-003",
        "rule_name": "术语和定义章节完整性",
        "scope": "terms",
        "severity": "minor",
        "source_ref": "built-in:SR-P0-003",
        "text": "需要术语定义的标准应提供术语和定义章节。",
    },
]


def configured_rules_path() -> Path:
    config = load_config()
    path = Path(config.standard_review.rules_md)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def load_review_rules() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load Markdown rules when available, otherwise return built-in P0 rules."""

    path = configured_rules_path()
    if not path.exists():
        return DEFAULT_RULES, {
            "rules_path": str(path),
            "rules_hash": "",
            "rules_source": "built-in",
        }
    text = path.read_text(encoding="utf-8", errors="ignore")
    rules = _parse_rules_markdown(text, path)
    if not rules:
        rules = DEFAULT_RULES
    return rules, {
        "rules_path": str(path),
        "rules_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "rules_source": "markdown",
    }


def _parse_rules_markdown(text: str, path: Path) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    body: list[str] = []
    for line in text.splitlines():
        heading = re.match(r"^#{2,6}\s*(?P<title>.+?)\s*$", line)
        if heading:
            if current:
                current["text"] = "\n".join(body).strip()
                rules.append(current)
            title = heading.group("title").strip()
            rule_id = _extract_rule_id(title) or f"R-{len(rules) + 1:03d}"
            current = {
                "rule_id": rule_id,
                "rule_name": re.sub(r"^[\[【]?[A-Za-z0-9_.-]+[\]】]?\s*", "", title).strip()
                or title,
                "scope": _infer_scope(title),
                "severity": "major",
                "source_ref": f"{path.as_posix()}#{rule_id}",
            }
            body = []
        elif current:
            body.append(line)
    if current:
        current["text"] = "\n".join(body).strip()
        rules.append(current)
    return rules


def _extract_rule_id(title: str) -> str | None:
    match = re.search(r"([A-Z]{1,4}[-_]\d{1,4}|R-\d{1,4})", title, flags=re.I)
    return match.group(1).upper() if match else None


def _infer_scope(value: str) -> str:
    if "范围" in value:
        return "scope"
    if "引用" in value:
        return "normative_references"
    if "术语" in value or "定义" in value:
        return "terms"
    if "格式" in value or "编号" in value or "目次" in value:
        return "format"
    return "full_document"

