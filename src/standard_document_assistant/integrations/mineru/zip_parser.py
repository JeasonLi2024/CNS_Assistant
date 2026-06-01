"""Parse MinerU ZIP responses and persist artifacts."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

from standard_document_assistant.integrations.mineru.images import (
    build_content_list_name_suggestions,
    collect_zip_image_bytes,
    persist_renamed_images,
    relative_image_ref_prefix,
    rewrite_markdown_image_refs,
)
from standard_document_assistant.integrations.mineru.naming import (
    extract_cover_metadata,
    has_pdf_info_payload,
    markdown_base_name,
    markdown_category,
    prepend_cover_info,
)
from standard_document_assistant.pathing import allocate_unique_path, host_to_virtual_path, safe_name


def _decode_json(raw: str) -> Any:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


def _first_entry(names: list[str], predicate) -> str | None:
    for name in names:
        if predicate(name):
            return name
    return None


def _is_middle_json_entry(name: str) -> bool:
    lowered = name.lower()
    return lowered.endswith("_middle.json") or lowered.endswith("/middle.json")


def _is_layout_json_entry(name: str) -> bool:
    return Path(name).name.lower() == "layout.json"


def _is_content_list_entry(name: str) -> bool:
    lowered = name.lower()
    return lowered.endswith(".json") and "content_list" in lowered and "v2" not in lowered


def parse_result_zip(
    *,
    zip_bytes: bytes,
    source_stem: str,
    output_root: Path,
    return_images: bool,
    save_middle_json: bool,
    save_content_list: bool,
) -> dict[str, Any]:
    """Persist Markdown, optional JSON sidecars, images, and path metadata."""

    zip_dir = output_root / "zip"
    md_root = output_root / "md"
    image_root = output_root / "images"
    json_root = output_root / "json"
    zip_dir.mkdir(parents=True, exist_ok=True)

    image_output_dir: Path | None = None
    warnings: list[str] = []

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        names = archive.namelist()
        md_entry = _first_entry(
            names,
            lambda name: name.lower().endswith(".md") and not name.lower().endswith("_middle.json"),
        )
        if not md_entry:
            raise RuntimeError("MinerU ZIP 中未找到 Markdown 文件。")
        middle_entry = _first_entry(names, _is_middle_json_entry)
        layout_entry = _first_entry(names, _is_layout_json_entry)
        content_entry = _first_entry(names, _is_content_list_entry)
        raw_markdown = archive.read(md_entry).decode("utf-8", errors="ignore")
        middle_raw = archive.read(middle_entry).decode("utf-8", errors="ignore") if middle_entry else ""
        layout_raw = archive.read(layout_entry).decode("utf-8", errors="ignore") if layout_entry else ""
        content_raw = archive.read(content_entry).decode("utf-8", errors="ignore") if content_entry else ""
        middle_json = _decode_json(middle_raw)
        layout_json = _decode_json(layout_raw)
        content_list = _decode_json(content_raw)
        cover_source = middle_json if has_pdf_info_payload(middle_json) else {}
        cover_metadata = extract_cover_metadata(
            cover_source,
            raw_markdown,
            layout_json=layout_json if has_pdf_info_payload(layout_json) else None,
            content_list=content_list,
        )

        base_name = markdown_base_name(source_stem, cover_metadata)
        category = markdown_category(cover_metadata)
        md_path = allocate_unique_path(md_root / category, base_name, ".md")
        image_output_dir = image_root / safe_name(base_name)

        markdown = raw_markdown
        if return_images:
            image_data = collect_zip_image_bytes(archive, names)
            if image_data:
                name_suggestions = build_content_list_name_suggestions(content_list, raw_markdown)
                rename_map, image_warnings = persist_renamed_images(
                    image_data=image_data,
                    output_dir=image_output_dir,
                    name_suggestions=name_suggestions,
                )
                warnings.extend(image_warnings)
                if rename_map:
                    rel_prefix = relative_image_ref_prefix(
                        md_parent=md_path.parent,
                        image_root=image_root,
                        image_subdir=image_output_dir,
                    )
                    markdown = rewrite_markdown_image_refs(
                        markdown,
                        rename_map,
                        rel_image_prefix=rel_prefix,
                    )

        markdown = prepend_cover_info(markdown, cover_metadata)
        md_path.write_text(markdown, encoding="utf-8")

        artifacts = [
            {"type": "markdown", "virtual_path": host_to_virtual_path(md_path), "description": "MinerU Markdown"},
        ]
        middle_path = None
        content_path = None
        if save_middle_json and (middle_raw or layout_raw):
            json_root.mkdir(parents=True, exist_ok=True)
            if middle_raw:
                middle_path = json_root / f"middle_{safe_name(source_stem)}.json"
                middle_path.write_text(
                    json.dumps(middle_json, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            elif layout_raw:
                middle_path = json_root / f"layout_{safe_name(source_stem)}.json"
                middle_path.write_text(
                    json.dumps(layout_json, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            artifacts.append(
                {
                    "type": "middle_json",
                    "virtual_path": host_to_virtual_path(middle_path),
                    "description": "MinerU middle_json",
                }
            )
        if save_content_list and content_raw:
            json_root.mkdir(parents=True, exist_ok=True)
            content_path = json_root / f"content_list_{safe_name(source_stem)}.json"
            content_path.write_text(
                json.dumps(content_list, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            artifacts.append(
                {
                    "type": "content_list",
                    "virtual_path": host_to_virtual_path(content_path),
                    "description": "MinerU content_list",
                }
            )
        if return_images and image_output_dir is not None and image_output_dir.exists():
            artifacts.append(
                {
                    "type": "image_root",
                    "virtual_path": host_to_virtual_path(image_output_dir) + "/",
                    "description": "MinerU images",
                }
            )

    return {
        "md_path": md_path,
        "middle_json_path": middle_path,
        "content_list_path": content_path,
        "image_root": image_output_dir if return_images else None,
        "cover_metadata": cover_metadata,
        "artifacts": artifacts,
        "md_category": category,
        "warnings": warnings,
    }
