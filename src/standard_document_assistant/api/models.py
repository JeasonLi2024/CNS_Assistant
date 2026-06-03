"""Request and response models for the FastAPI BFF."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class CreateThreadRequest(BaseModel):
    thread_id: str | None = Field(default=None, description="Optional caller-provided thread ID.")
    metadata: dict[str, Any] | None = Field(default=None, description="Optional thread metadata.")


class RunStreamRequest(BaseModel):
    message: str | None = Field(default=None, description="User message to send to the assistant.")
    input: dict[str, Any] | None = Field(
        default=None,
        description="Raw LangGraph input. If omitted, message is wrapped as a chat message.",
    )
    assistant_id: str | None = Field(default=None, description="Assistant ID. Defaults to agent.")
    stream_modes: list[str] | None = Field(
        default=None,
        description="LangGraph stream modes. Defaults to custom + updates.",
    )
    stream_subgraphs: bool | None = Field(
        default=None,
        description="Whether to include subgraph/subagent stream events.",
    )
    metadata: dict[str, Any] | None = Field(default=None, description="Optional run metadata.")
    context: dict[str, Any] | None = Field(default=None, description="Optional runtime context.")


class ResumeRunRequest(BaseModel):
    action: Literal["approve", "reject", "edit"] = Field(description="Human decision.")
    message: str | None = Field(default=None, description="Optional edit/reason message.")
    assistant_id: str | None = Field(default=None, description="Assistant ID. Defaults to agent.")
    metadata: dict[str, Any] | None = Field(default=None, description="Optional run metadata.")
    context: dict[str, Any] | None = Field(default=None, description="Optional runtime context.")


class StandardReviewRequest(BaseModel):
    file_path: str = Field(
        description="Uploaded standard document virtual path, e.g. /workspace/input/uploads/.../a.pdf."
    )
    instruction: str | None = Field(
        default=None,
        description="Optional extra review instruction.",
    )
    assistant_id: str | None = Field(default=None, description="Assistant ID. Defaults to agent.")
