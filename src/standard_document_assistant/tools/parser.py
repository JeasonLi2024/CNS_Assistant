"""Document parsing tool backed by MinerU."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Annotated, Any

from langchain.tools import ToolRuntime
from langchain_core.tools import InjectedToolArg, StructuredTool

from standard_document_assistant.config import load_config
from standard_document_assistant.constants import SAMPLES_DIR, UPLOADS_DIR
from standard_document_assistant.integrations.mineru.client import request_parse_file
from standard_document_assistant.integrations.mineru.zip_parser import parse_result_zip
from standard_document_assistant.pathing import (
    allocate_unique_path,
    host_to_virtual_path,
    mineru_output_root,
    resolve_workspace_read_path,
    safe_name,
    utc_now_iso,
    write_json,
)
from standard_document_assistant.schemas import ArtifactManifest, ArtifactRef, MinerUParseResult
from standard_document_assistant.tracing import (
    PARSE_DOCUMENT_WITH_MINERU_TOOL_NAME,
    PARSE_FILE_WITH_MINERU_TOOL_NAME,
)


SUPPORTED_MINERU_SUFFIXES = {".pdf", ".docx"}


def _trace_metadata(*, runtime: ToolRuntime | None, file_path: str) -> dict[str, Any]:
    extra: dict[str, Any] = {"source_virtual_path": file_path}
    if runtime is not None:
        configurable = (runtime.config or {}).get("configurable") or {}
        if isinstance(configurable, dict) and configurable.get("thread_id"):
            extra["thread_id"] = configurable["thread_id"]
    return extra


def _parse_file_with_mineru_sync(
    file_path: str,
    *,
    return_images: bool | None = None,
    save_zip_archive: bool | None = None,
    save_middle_json: bool | None = None,
    save_content_list: bool | None = None,
    skip_if_zip_exists: bool | None = None,
    output_subdir: str | None = None,
    runtime: Annotated[ToolRuntime | None, InjectedToolArg] = None,
) -> dict[str, Any]:
    """Parse an uploaded PDF or Word document into Markdown and artifacts using MinerU."""

    started = time.perf_counter()
    config = load_config()
    mineru_config = config.mineru
    return_images = mineru_config.return_images if return_images is None else return_images
    save_zip_archive = mineru_config.save_zip_archive if save_zip_archive is None else save_zip_archive
    save_middle_json = mineru_config.save_middle_json if save_middle_json is None else save_middle_json
    save_content_list = (
        mineru_config.save_content_list if save_content_list is None else save_content_list
    )
    skip_if_zip_exists = (
        mineru_config.skip_if_zip_exists if skip_if_zip_exists is None else skip_if_zip_exists
    )
    output_root = mineru_output_root(output_subdir or mineru_config.output_subdir)
    source_path, source_virtual = resolve_workspace_read_path(
        file_path,
        allowed_roots=[UPLOADS_DIR, SAMPLES_DIR],
        suffixes=SUPPORTED_MINERU_SUFFIXES,
    )
    max_bytes = mineru_config.max_pdf_size_mb * 1024 * 1024
    if source_path.stat().st_size > max_bytes:
        raise ValueError(f"文件超过 MinerU 大小限制：{mineru_config.max_pdf_size_mb}MB")

    zip_dir = output_root / "zip"
    zip_path = zip_dir / f"{safe_name(source_path.stem)}.zip"
    resumed = False
    if skip_if_zip_exists and zip_path.exists():
        zip_bytes = zip_path.read_bytes()
        resumed = True
    else:
        zip_bytes = request_parse_file(source_path, mineru_config, return_images=return_images)
        if save_zip_archive:
            zip_dir.mkdir(parents=True, exist_ok=True)
            zip_path.write_bytes(zip_bytes)

    parsed = parse_result_zip(
        zip_bytes=zip_bytes,
        source_stem=source_path.stem,
        output_root=output_root,
        return_images=return_images,
        save_middle_json=save_middle_json,
        save_content_list=save_content_list,
    )
    artifacts = [ArtifactRef.model_validate(item) for item in parsed["artifacts"]]
    if save_zip_archive and zip_path.exists():
        zip_ref = ArtifactRef(
            type="zip",
            virtual_path=host_to_virtual_path(zip_path),
            description="MinerU 原始 ZIP",
        )
        artifacts.append(zip_ref)
    primary = next((item for item in artifacts if item.type == "markdown"), None)
    manifest_path = allocate_unique_path(
        output_root / "manifests", safe_name(Path(parsed["md_path"]).stem) + "_parse_manifest", ".json"
    )
    parse_warnings = list(parsed.get("warnings") or [])
    manifest = ArtifactManifest(
        tool=PARSE_FILE_WITH_MINERU_TOOL_NAME,
        status="ok",
        source_virtual_path=source_virtual,
        primary_artifact=primary,
        artifacts=artifacts,
        warnings=parse_warnings,
        error="",
        created_at=utc_now_iso(),
    )
    manifest_payload = manifest.model_dump()
    manifest_payload["cover_metadata"] = parsed["cover_metadata"]
    manifest_payload["trace"] = _trace_metadata(runtime=runtime, file_path=source_virtual)
    write_json(manifest_path, manifest_payload)
    result = MinerUParseResult(
        status="ok",
        source_virtual_path=source_virtual,
        virtual_md_path=primary.virtual_path if primary else "",
        virtual_manifest_path=host_to_virtual_path(manifest_path),
        virtual_zip_path=host_to_virtual_path(zip_path) if zip_path.exists() else "",
        virtual_image_root=(
            host_to_virtual_path(parsed["image_root"]) + "/" if parsed.get("image_root") else ""
        ),
        cover_metadata=parsed["cover_metadata"],
        warnings=parse_warnings,
        duration_ms=int((time.perf_counter() - started) * 1000),
        resumed_from_zip=resumed,
    )
    return result.model_dump()


parse_file_with_mineru = StructuredTool.from_function(
    func=_parse_file_with_mineru_sync,
    name=PARSE_FILE_WITH_MINERU_TOOL_NAME,
    description=(
        "Parse an uploaded PDF or Word standard document via MinerU into Markdown, "
        "images, JSON sidecars, and a manifest under /workspace/output/mineru."
    ),
)

parse_document_with_mineru = StructuredTool.from_function(
    func=_parse_file_with_mineru_sync,
    name=PARSE_DOCUMENT_WITH_MINERU_TOOL_NAME,
    description=(
        "Parse an uploaded PDF or Word standard document via MinerU into Markdown, "
        "images, JSON sidecars, and a manifest under /workspace/output/mineru. "
        "Use this standalone preprocessing tool before standard review."
    ),
)


def parse_pdf_with_mineru(file_path: str, **kwargs: Any) -> dict[str, Any]:
    """Backward-compatible wrapper for callers that still use the old Python function."""

    return _parse_file_with_mineru_sync(file_path, **kwargs)
