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


class ReviewOptions(BaseModel):
    """Structured options for direct standard review calls."""

    mode: Literal[
        "content_and_format",
        "content_only",
        "format_only",
        "full_document_content",
        "scoped_content",
        "line_range_content",
    ] = Field(
        default="content_only",
        description="Review mode. Generation workflows usually use content_only or scoped_content.",
    )
    target_scopes: list[str] | None = Field(
        default=None,
        description="Canonical or Chinese scope names, e.g. ['scope', 'normative_references'].",
    )
    line_start: int | None = Field(default=None, ge=1, description="1-based start line.")
    line_end: int | None = Field(default=None, ge=1, description="1-based end line, inclusive.")
    partial_mode: Literal["sectional", "full_document", "format_only"] | None = Field(
        default=None,
        description="Override standard_review partial_mode.",
    )
    top_k: int | None = Field(default=None, ge=1, description="Rule retrieval count per scope.")
    force_rebuild_index: bool | None = Field(default=None, description="Force review rule index rebuild.")
    disable_widen: bool = Field(
        default=False,
        description="Prevent quality gate from widening a scoped review to full-document review.",
    )
    max_review_rounds: int | None = Field(
        default=None,
        ge=0,
        description="Override max review rounds. disable_widen sets this to 0.",
    )


class DirectStandardReviewRequest(BaseModel):
    """Machine-oriented standard review request."""

    thread_id: str | None = Field(default=None, description="Thread ID for artifact registration.")
    file_path: str = Field(description="Markdown path for content review or PDF/DOCX path for format_only.")
    source_path: str | None = Field(
        default=None,
        description="Optional PDF/DOCX source path for content_and_format mode.",
    )
    manifest_path: str | None = Field(default=None, description="Optional MinerU manifest path.")
    output_subdir: str | None = Field(default=None, description="Review output subdir.")
    trace_id: str | None = Field(default=None, description="External trace ID.")
    instruction: str | None = Field(
        default=None,
        description="Optional note kept for caller-side traceability. Direct graph call does not prompt on it.",
    )
    review_options: ReviewOptions = Field(default_factory=ReviewOptions)
    return_report_content: bool = Field(default=True, description="Return Markdown report content.")
    return_result_json: bool = Field(default=True, description="Return parsed audit result JSON.")
