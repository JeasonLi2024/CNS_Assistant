"""Configuration loading for the standard document assistant."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from standard_document_assistant.constants import PROJECT_ROOT


@dataclass(frozen=True)
class ModelConfig:
    provider: str = "qwen"
    class_path: str = "langchain_qwq.ChatQwen"
    model: str = "qwen3.7-max"
    temperature: float = 0.0
    max_tokens: int = 8000
    timeout: int = 60
    max_retries: int = 2


@dataclass(frozen=True)
class RuntimeConfig:
    app_name: str = "standard-document-assistant"
    default_language: str = "zh-CN"
    streaming: bool = True
    transport: str = "sse"
    require_human_approval: bool = True
    default_thread_prefix: str = "standard-doc"


@dataclass(frozen=True)
class WorkspaceConfig:
    uploads_dir: str = "workspace/input/uploads"
    output_dir: str = "workspace/output"
    tmp_dir: str = "workspace/tmp"
    max_upload_size_mb: int = 100
    allowed_upload_suffixes: tuple[str, ...] = (".pdf", ".md", ".markdown", ".txt")


@dataclass(frozen=True)
class MinerUConfig:
    api_base_url: str = ""
    request_timeout: int = 600
    max_pdf_size_mb: int = 100
    return_images: bool = True
    save_zip_archive: bool = True
    save_middle_json: bool = False
    save_content_list: bool = False
    skip_if_zip_exists: bool = True
    output_subdir: str = ""
    request_options: dict[str, Any] = field(
        default_factory=lambda: {
            "backend": "pipeline",
            "lang_list": "ch",
            "parse_method": "auto",
            "formula_enable": "true",
            "table_enable": "true",
            "return_middle_json": "true",
            "return_content_list": "true",
            "response_format_zip": "true",
        }
    )


@dataclass(frozen=True)
class MetadataExtractionConfig:
    default_scope_mode: str = "metadata"
    scoped_text_max_bytes: int = 524288
    strict_validation: bool = False
    write_artifacts: bool = True
    model_provider: str = "dashscope-compatible"
    model: str = "qwen-max"
    timeout: int = 120
    max_retries: int = 2


@dataclass(frozen=True)
class AssistantConfig:
    runtime: RuntimeConfig = RuntimeConfig()
    primary_model: ModelConfig = ModelConfig()
    workspace: WorkspaceConfig = WorkspaceConfig()
    mineru: MinerUConfig = MinerUConfig()
    metadata_extraction: MetadataExtractionConfig = MetadataExtractionConfig()
    langsmith_project: str = "standard-document-assistant"


def load_dotenv_if_available() -> None:
    """Load .env if python-dotenv is installed."""

    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(PROJECT_ROOT / ".env")


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def load_config(path: Path | None = None) -> AssistantConfig:
    """Load config.yaml if PyYAML is available, otherwise return documented defaults."""

    load_dotenv_if_available()
    data = _read_yaml(path or PROJECT_ROOT / "config.yaml")
    app_data = data.get("app", {})
    runtime_data = data.get("runtime", {})
    workspace_data = data.get("workspace", {})
    mineru_data = data.get("mineru", {})
    metadata_data = data.get("metadata_extraction", {})
    primary_data = data.get("models", {}).get("primary", {})

    runtime = RuntimeConfig(
        app_name=app_data.get("name", RuntimeConfig.app_name),
        default_language=app_data.get("default_language", RuntimeConfig.default_language),
        streaming=runtime_data.get("streaming", RuntimeConfig.streaming),
        transport=runtime_data.get("transport", RuntimeConfig.transport),
        require_human_approval=runtime_data.get(
            "require_human_approval", RuntimeConfig.require_human_approval
        ),
        default_thread_prefix=runtime_data.get(
            "default_thread_prefix", RuntimeConfig.default_thread_prefix
        ),
    )
    primary = ModelConfig(
        provider=primary_data.get("provider", ModelConfig.provider),
        class_path=primary_data.get("class", ModelConfig.class_path),
        model=primary_data.get("model", ModelConfig.model),
        temperature=float(primary_data.get("temperature", ModelConfig.temperature)),
        max_tokens=int(primary_data.get("max_tokens", ModelConfig.max_tokens)),
        timeout=int(primary_data.get("timeout", ModelConfig.timeout)),
        max_retries=int(primary_data.get("max_retries", ModelConfig.max_retries)),
    )
    workspace = WorkspaceConfig(
        uploads_dir=workspace_data.get("uploads_dir", WorkspaceConfig.uploads_dir),
        output_dir=workspace_data.get("output_dir", WorkspaceConfig.output_dir),
        tmp_dir=workspace_data.get("tmp_dir", WorkspaceConfig.tmp_dir),
        max_upload_size_mb=int(
            workspace_data.get("max_upload_size_mb", WorkspaceConfig.max_upload_size_mb)
        ),
        allowed_upload_suffixes=tuple(
            str(item).lower()
            for item in workspace_data.get(
                "allowed_upload_suffixes", WorkspaceConfig.allowed_upload_suffixes
            )
        ),
    )
    mineru = MinerUConfig(
        api_base_url=os.getenv("MINERU_API_BASE_URL", mineru_data.get("api_base_url", "")),
        request_timeout=int(
            os.getenv(
                "MINERU_REQUEST_TIMEOUT",
                mineru_data.get("request_timeout", MinerUConfig.request_timeout),
            )
        ),
        max_pdf_size_mb=int(
            os.getenv(
                "MINERU_MAX_PDF_SIZE_MB",
                mineru_data.get("max_pdf_size_mb", MinerUConfig.max_pdf_size_mb),
            )
        ),
        return_images=bool(mineru_data.get("return_images", MinerUConfig.return_images)),
        save_zip_archive=bool(
            mineru_data.get("save_zip_archive", MinerUConfig.save_zip_archive)
        ),
        save_middle_json=bool(
            mineru_data.get("save_middle_json", MinerUConfig.save_middle_json)
        ),
        save_content_list=bool(
            mineru_data.get("save_content_list", MinerUConfig.save_content_list)
        ),
        skip_if_zip_exists=bool(
            mineru_data.get("skip_if_zip_exists", MinerUConfig.skip_if_zip_exists)
        ),
        output_subdir=mineru_data.get("output_subdir", MinerUConfig.output_subdir),
        request_options=mineru_data.get("request_options", MinerUConfig().request_options),
    )
    metadata_model = metadata_data.get("model", {})
    metadata_extraction = MetadataExtractionConfig(
        default_scope_mode=metadata_data.get(
            "default_scope_mode", MetadataExtractionConfig.default_scope_mode
        ),
        scoped_text_max_bytes=int(
            metadata_data.get(
                "scoped_text_max_bytes", MetadataExtractionConfig.scoped_text_max_bytes
            )
        ),
        strict_validation=bool(
            metadata_data.get("strict_validation", MetadataExtractionConfig.strict_validation)
        ),
        write_artifacts=bool(
            metadata_data.get("write_artifacts", MetadataExtractionConfig.write_artifacts)
        ),
        model_provider=metadata_model.get(
            "provider", MetadataExtractionConfig.model_provider
        ),
        model=metadata_model.get("model", MetadataExtractionConfig.model),
        timeout=int(metadata_model.get("timeout", MetadataExtractionConfig.timeout)),
        max_retries=int(metadata_model.get("max_retries", MetadataExtractionConfig.max_retries)),
    )
    return AssistantConfig(
        runtime=runtime,
        primary_model=primary,
        workspace=workspace,
        mineru=mineru,
        metadata_extraction=metadata_extraction,
        langsmith_project=os.getenv("LANGSMITH_PROJECT", runtime.app_name),
    )


def build_qwen_model(config: ModelConfig, *, strict: bool = False):
    """Build a ChatQwen model.

    In non-strict local checks, fall back to a fake chat model when the Qwen
    dependency or API key is absent. Use strict=True for real runs.
    """

    try:
        from langchain_qwq import ChatQwen
    except ImportError as exc:
        if strict:
            raise RuntimeError("缺少 langchain-qwq，无法创建 Qwen 模型。") from exc
        from langchain_core.language_models.fake_chat_models import FakeListChatModel

        return FakeListChatModel(responses=["本地结构验证模型：未安装 langchain-qwq。"])

    if not os.getenv("DASHSCOPE_API_KEY"):
        if strict:
            raise RuntimeError("缺少 DASHSCOPE_API_KEY，无法调用 Qwen 模型。")
        from langchain_core.language_models.fake_chat_models import FakeListChatModel

        return FakeListChatModel(responses=["本地结构验证模型：未配置 DASHSCOPE_API_KEY。"])

    return ChatQwen(
        model=config.model,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        timeout=config.timeout,
        max_retries=config.max_retries,
    )
