"""Validation and memory proposal tools."""

from __future__ import annotations

import json
from typing import Any

from standard_document_assistant.schemas import (
    AgentResult,
    ArtifactManifest,
    MemoryUpdateProposal,
    MetadataExtractionResult,
    MinerUParseResult,
    ReviewIssue,
    ReviewToolResult,
    StandardMetadataExtraction,
    UploadedFileRecord,
)


def validate_output_schema(payload: str | dict[str, Any], schema_name: str = "AgentResult") -> dict[str, Any]:
    """Validate a JSON payload against a supported Pydantic schema."""

    data = json.loads(payload) if isinstance(payload, str) else payload
    schemas = {
        "AgentResult": AgentResult,
        "ArtifactManifest": ArtifactManifest,
        "MemoryUpdateProposal": MemoryUpdateProposal,
        "MetadataExtractionResult": MetadataExtractionResult,
        "MinerUParseResult": MinerUParseResult,
        "ReviewIssue": ReviewIssue,
        "ReviewToolResult": ReviewToolResult,
        "StandardMetadataExtraction": StandardMetadataExtraction,
        "UploadedFileRecord": UploadedFileRecord,
    }
    schema = schemas.get(schema_name)
    if schema is None:
        raise ValueError(f"未知 schema：{schema_name}")
    try:
        parsed = schema.model_validate(data)
    except Exception as exc:
        return {"valid": False, "schema": schema_name, "errors": str(exc)}
    return {"valid": True, "schema": schema_name, "data": parsed.model_dump()}


def propose_memory_update(target_path: str, content: str, reason: str) -> dict[str, Any]:
    """Create a human-reviewable memory update proposal without persisting it."""

    if not target_path.startswith("/memories/"):
        raise ValueError("长期记忆提案的目标路径必须位于 /memories/ 下。")
    if "\\" in target_path or ":" in target_path:
        raise ValueError("长期记忆提案必须使用 /memories/ 虚拟路径，不能使用宿主机绝对路径。")
    lowered = target_path.lower()
    if ".env" in lowered or "secret" in lowered or "credential" in lowered:
        raise ValueError("拒绝对敏感路径提出记忆更新。")
    proposal = MemoryUpdateProposal(
        target_path=target_path,
        content=content,
        reason=reason,
        requires_approval=True,
    )
    payload = proposal.model_dump()
    payload.update(
        {
            "persistence_mode": "proposal_only",
            "next_step": "审批通过后，由应用层在用户隔离的 Store namespace 中合并并写入该提案。",
            "message": "这是记忆更新提案，不会直接写入长期记忆或本地 memories/ 目录。",
        }
    )
    return payload
