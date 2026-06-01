"""Structured output schemas for the standard document assistant."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Artifact(BaseModel):
    """A file or persisted output produced by the agent."""

    path: str = Field(description="产物路径")
    type: str = Field(description="产物类型，例如 review_report、draft、converted_doc、extracted_json")
    description: str = Field(description="产物说明")


class UploadedFileRecord(BaseModel):
    """A file saved by the application upload layer."""

    original_filename: str = Field(description="用户上传时的原始文件名")
    stored_filename: str = Field(description="保存后的安全文件名")
    virtual_path: str = Field(description="Deep Agents 虚拟路径")
    host_path: str = Field(description="宿主机路径，仅供应用层和测试使用")
    suffix: str = Field(description="文件后缀")
    size_bytes: int = Field(ge=0, description="文件大小")
    sha256: str = Field(description="文件 SHA256")
    content_type: str = Field(default="", description="上传 content type")
    created_at: str = Field(description="保存时间")


class ArtifactRef(BaseModel):
    """A reference to a persisted artifact."""

    type: str = Field(description="产物类型")
    virtual_path: str = Field(description="Deep Agents 虚拟路径")
    description: str = Field(default="", description="产物说明")


class ArtifactManifest(BaseModel):
    """Manifest written by business tools for downstream steps."""

    tool: str = Field(description="生成该 manifest 的工具名")
    status: Literal["ok", "failed"] = Field(description="工具执行状态")
    source_virtual_path: str = Field(default="", description="源文件虚拟路径")
    primary_artifact: ArtifactRef | None = Field(default=None, description="主产物")
    artifacts: list[ArtifactRef] = Field(default_factory=list, description="全部产物")
    warnings: list[str] = Field(default_factory=list, description="非致命警告")
    error: str = Field(default="", description="失败原因")
    created_at: str = Field(description="生成时间")


class MinerUParseResult(BaseModel):
    """Public result returned by parse_file_with_mineru."""

    status: Literal["ok", "failed"] = "ok"
    source_virtual_path: str = ""
    virtual_md_path: str = ""
    virtual_manifest_path: str = ""
    virtual_zip_path: str = ""
    virtual_image_root: str = ""
    cover_metadata: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    error: str = ""
    duration_ms: int = 0
    resumed_from_zip: bool = False


class StandardMetadataExtraction(BaseModel):
    """国标元数据抽取结果。字段缺失时保持空字符串或空列表，禁止编造。"""

    ics: str = ""
    ccs: str = ""
    标准层级: str = ""
    标准号: str = ""
    代替标准号: str = ""
    发布日期: str = ""
    实施日期: str = ""
    标准中文名称: str = ""
    标准英文名称: str = ""
    采标信息: str = ""
    提出单位: list[str] = Field(default_factory=list)
    归口单位: list[str] = Field(default_factory=list)
    起草单位: list[str] = Field(default_factory=list)
    起草人: list[str] = Field(default_factory=list)
    引用文件: list[str] = Field(default_factory=list)
    专业术语: list[str] = Field(default_factory=list)
    标准性质: str = ""
    制修订: Literal["制订", "修订", ""] = ""
    源文件: str = ""


class PersistedArtifactRecord(BaseModel):
    """A business artifact registered by the application layer for download/SSE."""

    artifact_id: str = Field(description="产物唯一 ID，用于下载 URL")
    thread_id: str = Field(description="所属 thread")
    tool: str = Field(default="", description="生成该产物的工具名")
    artifact_type: str = Field(description="产物类型")
    description: str = Field(default="", description="产物说明")
    virtual_path: str = Field(description="Deep Agents 虚拟路径")
    source_virtual_path: str = Field(default="", description="源文件虚拟路径")
    stored_filename: str = Field(description="保存后的文件名")
    host_path: str = Field(description="宿主机路径，仅供应用层读文件")
    suffix: str = Field(description="文件后缀")
    size_bytes: int = Field(ge=0, description="文件大小")
    sha256: str = Field(description="文件 SHA256")
    content_type: str = Field(default="application/octet-stream", description="Content-Type")
    download_url: str | None = Field(default=None, description="HTTP 下载地址")
    created_at: str = Field(description="登记时间")


class ArtifactDownload(BaseModel):
    """How a persisted artifact can be downloaded or opened locally."""

    artifact_id: str | None = Field(default=None, description="应用层登记后的产物 ID")
    virtual_path: str = Field(description="Deep Agents 虚拟路径")
    host_path: str = Field(description="宿主机绝对路径，本地调试可直接打开")
    file_name: str = Field(description="文件名")
    download_url: str | None = Field(default=None, description="HTTP 下载地址")
    local_open_hint: str = Field(default="", description="本地访问提示")


class MetadataExtractionResult(BaseModel):
    """Public result returned by extract_standard_metadata."""

    status: Literal["ok", "failed"] = "ok"
    source_virtual_path: str = ""
    virtual_output_path: str = ""
    virtual_manifest_path: str = ""
    virtual_annotated_path: str = ""
    virtual_normalized_path: str = ""
    aggregated_summary: dict[str, Any] = Field(default_factory=dict)
    aggregated: dict[str, Any] = Field(default_factory=dict, description="完整聚合 JSON，供主 Agent 直接使用")
    validation: dict[str, Any] = Field(default_factory=dict)
    quality_warnings: list[str] = Field(default_factory=list, description="抽取质量提醒，不自动改 JSON")
    scoped_text_chars: int = 0
    extracted_items: int = 0
    download: ArtifactDownload | None = Field(default=None, description="主 JSON 产物下载信息")
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ReviewIssue(BaseModel):
    """A standard review issue with traceable rule and evidence references."""

    issue_id: str = ""
    rule_id: str = ""
    rule_name: str = ""
    scope: str = ""
    route: str = "standard_review"
    audit_track: Literal["content", "format_source"] = "content"
    severity: Literal["critical", "major", "minor", "info"] = "info"
    status: Literal["pass", "fail", "warn", "insufficient_context", "llm_error"] = "warn"
    expected: str = ""
    actual: str = ""
    evidence_text: str = ""
    source_ref: str = ""
    suggestion: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    llm_reasoning: str = ""


class ReviewSummary(BaseModel):
    """Aggregated review counters returned by standard review tools."""

    total_issues: int = 0
    failed: int = 0
    warn: int = 0
    insufficient_context: int = 0
    by_severity: dict[str, int] = Field(default_factory=dict)
    by_track: dict[str, int] = Field(default_factory=dict)


class ReviewToolResult(BaseModel):
    """Public result returned by standard review tools."""

    status: Literal["success", "failed"] = "success"
    job_id: str = ""
    trace_id: str = ""
    trace_path: str = ""
    summary: ReviewSummary = Field(default_factory=ReviewSummary)
    artifacts: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    error: str = ""


class Finding(BaseModel):
    """A review finding tied to evidence in a standard document."""

    severity: Literal["critical", "high", "medium", "low", "info"] = Field(
        description="问题级别"
    )
    location: str = Field(description="章节、页码或原文片段位置")
    issue: str = Field(description="发现的问题")
    suggestion: str = Field(description="修改建议")
    evidence: str = Field(description="审核依据或引用片段")


class ExtractedInformation(BaseModel):
    """Key fields extracted from a standard document."""

    standard_name: str = Field(default="不确定", description="标准名称")
    scope: str = Field(default="不确定", description="范围")
    terms: list[str] = Field(default_factory=list, description="术语和定义")
    references: list[str] = Field(default_factory=list, description="规范性引用文件")
    clauses: list[str] = Field(default_factory=list, description="条款或章节结构")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="整体置信度")


class AgentResult(BaseModel):
    """Final structured response returned by the main Deep Agent."""

    summary: str = Field(description="最终结论")
    task_type: Literal["parse", "review", "draft", "convert", "extract", "search", "mixed"] = Field(
        description="任务类型"
    )
    artifacts: list[Artifact] = Field(default_factory=list, description="生成文件")
    findings: list[Finding] = Field(default_factory=list, description="审核发现")
    next_steps: list[str] = Field(default_factory=list, description="建议后续动作")


class MemoryUpdateProposal(BaseModel):
    """A proposed long-term memory update that requires human approval."""

    target_path: str = Field(description="目标记忆文件路径")
    content: str = Field(description="建议写入或修改的内容")
    reason: str = Field(description="提出该记忆更新的原因")
    requires_approval: bool = Field(default=True, description="是否需要人工审批")
