"""MinerU image extraction, semantic renaming, and Markdown reference rewriting."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

_FIG_PREFIX_RE = re.compile(r"^\s*图\s*[^\s].*")
_TABLE_PREFIX_RE = re.compile(r"^\s*表\s*[^\s].*")
_LETTER_LABEL_RE = re.compile(r"^\s*[a-z]\s*[)）]\s*$")
_TRAILING_PUNCT_RE = re.compile(r"[。！？；：,.;!?]+$")
_HASH_IMAGE_NAME_RE = re.compile(r"^[a-f0-9]{16,}\.[a-z0-9]+$", re.I)
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")


def sanitize_filename_component(name: str) -> str:
    text = re.sub(r'[<>:"/\\|?*]', "_", str(name or ""))
    text = re.sub(r"\s+", " ", text).strip().rstrip(". ")
    return text


def strip_trailing_punct(text: str) -> str:
    value = "" if text is None else str(text).rstrip()
    return _TRAILING_PUNCT_RE.sub("", value).rstrip()


def iter_content_list_blocks(content_list: Any) -> list[dict[str, Any]]:
    if isinstance(content_list, list):
        return [item for item in content_list if isinstance(item, dict)]
    if isinstance(content_list, dict):
        for key in ("content_list", "items", "blocks", "data"):
            value = content_list.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def extract_first_prefixed_caption(captions: Any, prefix: str) -> str:
    if not isinstance(captions, list):
        return ""
    prefix_re = _FIG_PREFIX_RE if prefix == "图" else _TABLE_PREFIX_RE
    for item in captions:
        text = "" if item is None else str(item).strip()
        if text and prefix_re.match(text):
            return text
    return ""


def find_last_table_caption_before_md_anchor(md_content: str, anchor: str) -> str:
    if not md_content or not anchor:
        return ""
    pos = md_content.find(anchor)
    if pos < 0:
        return ""
    prefix = md_content[:pos]
    matches = list(re.finditer(r"^\s*表[^\n]*", prefix, flags=re.MULTILINE))
    if not matches:
        return ""
    return matches[-1].group(0).strip()


def build_content_list_name_suggestions(content_list: Any, md_content: str) -> dict[str, str]:
    """Map original image basename -> suggested filename stem (no extension)."""

    suggestions: dict[str, str] = {}
    last_text = ""

    for block in iter_content_list_blocks(content_list):
        block_type = str(block.get("type", "") or "").strip().lower()
        if block_type == "text":
            text_value = str(block.get("text", "") or "").strip()
            if text_value:
                last_text = text_value
            continue

        if block_type not in {"image", "table"}:
            continue

        img_path = str(block.get("img_path", "") or "").strip()
        if not img_path:
            continue
        old_name = Path(img_path.replace("\\", "/")).name
        if not old_name:
            continue

        suggested = ""
        if block_type == "image":
            captions = block.get("image_caption")
            suggested = extract_first_prefixed_caption(captions, "图")
            if not suggested and isinstance(captions, list):
                lettered = next(
                    (str(item).strip() for item in captions if _LETTER_LABEL_RE.match(str(item).strip())),
                    "",
                )
                if lettered and last_text:
                    suggested = f"{strip_trailing_punct(last_text)}{lettered}"
        else:
            captions = block.get("table_caption")
            suggested = extract_first_prefixed_caption(captions, "表")
            if not suggested and isinstance(captions, list) and captions:
                table_body = str(block.get("table_body", "") or "").strip()
                suggested = find_last_table_caption_before_md_anchor(md_content, table_body)

        if suggested:
            suggestions[old_name] = suggested

    return suggestions


def is_image_zip_entry(name: str) -> bool:
    if not name or name.endswith("/"):
        return False
    normalized = name.replace("\\", "/").lower()
    if not normalized.endswith(_IMAGE_SUFFIXES):
        return False
    if "/images/" in normalized or normalized.startswith("images/"):
        return True
    return normalized.count("/") <= 1 and normalized.endswith(_IMAGE_SUFFIXES)


def collect_zip_image_entries(names: list[str]) -> list[str]:
    """Return ZIP member paths for image files (local nested and precise flat layouts)."""

    entries = [name for name in names if is_image_zip_entry(name)]
    if entries:
        return sorted(entries)
    return sorted(
        name
        for name in names
        if not name.endswith("/") and name.lower().endswith(_IMAGE_SUFFIXES)
    )


def collect_zip_image_bytes(archive: Any, names: list[str]) -> dict[str, bytes]:
    """Return {basename: bytes} for all images in the ZIP."""

    image_data: dict[str, bytes] = {}
    for entry in collect_zip_image_entries(names):
        basename = Path(entry.replace("\\", "/")).name
        if not basename:
            continue
        image_data[basename] = archive.read(entry)
    return image_data


def _next_sequence_name(ext: str, used_names: set[str], output_dir: Path, counter: list[int]) -> str:
    while True:
        candidate = f"{counter[0]:03d}{ext}"
        counter[0] += 1
        if candidate in used_names:
            continue
        if (output_dir / candidate).exists():
            continue
        return candidate


def persist_renamed_images(
    *,
    image_data: dict[str, bytes],
    output_dir: Path,
    name_suggestions: dict[str, str],
    min_size_bytes: int = 100,
) -> tuple[dict[str, str], list[str]]:
    """Write images to output_dir; return {old_basename: new_basename} and warnings."""

    output_dir.mkdir(parents=True, exist_ok=True)
    rename_map: dict[str, str] = {}
    warnings: list[str] = []
    used_names: set[str] = set()
    seq_counter = [1]

    for old_name in sorted(image_data):
        payload = image_data[old_name]
        ext = Path(old_name).suffix or ".jpg"
        suggested_base = sanitize_filename_component(name_suggestions.get(old_name, ""))

        if suggested_base:
            idx = 0
            while True:
                suffix = "" if idx == 0 else f"（{idx}）"
                candidate = f"{suggested_base}{suffix}{ext}"
                if candidate in used_names or (output_dir / candidate).exists():
                    idx += 1
                    continue
                new_name = candidate
                break
        else:
            new_name = _next_sequence_name(ext, used_names, output_dir, seq_counter)

        used_names.add(new_name)
        target = output_dir / new_name
        target.write_bytes(payload)
        rename_map[old_name] = new_name

        if len(payload) <= min_size_bytes:
            warnings.append(f"图片几乎为空：{new_name} ({len(payload)} bytes)")

    return rename_map, warnings


def relative_image_ref_prefix(*, md_parent: Path, image_root: Path, image_subdir: Path) -> str:
    """Relative POSIX path from Markdown parent directory to the image subdirectory."""

    rel_images_root = os.path.relpath(image_root.resolve(), start=md_parent.resolve()).replace(
        "\\", "/"
    )
    return f"{rel_images_root}/{image_subdir.name}".replace("\\", "/")


def rewrite_markdown_image_refs(
    markdown: str,
    rename_map: dict[str, str],
    *,
    rel_image_prefix: str,
) -> str:
    """Replace MinerU image references with paths relative to the saved Markdown file."""

    if not markdown or not rename_map:
        return markdown

    prefix = rel_image_prefix.strip("/").replace("\\", "/")
    updated = markdown
    for old_name, new_name in sorted(rename_map.items(), key=lambda item: -len(item[0])):
        new_path = f"{prefix}/{new_name}".replace("\\", "/")
        while "//" in new_path:
            new_path = new_path.replace("//", "/")

        old_refs = [
            f"images/{old_name}",
            f"./images/{old_name}",
            f"/images/{old_name}",
        ]
        if _HASH_IMAGE_NAME_RE.match(old_name):
            old_refs.append(old_name)

        for old_ref in old_refs:
            updated = updated.replace(old_ref, new_path)

        pattern = re.compile(
            rf"(!\[[^\]]*\]\()([^)]*?){re.escape(old_name)}(\))",
            flags=re.IGNORECASE,
        )
        updated = pattern.sub(rf"\1{new_path}\3", updated)

    return updated
