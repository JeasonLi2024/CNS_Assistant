"""State schema for the metadata extraction graph."""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict


class MetadataExtractionState(TypedDict, total=False):
    source_path: str
    source_virtual_path: str
    markdown: str
    scope_mode: Literal["metadata", "full"]
    output_filename: str
    write_artifacts: bool
    output_path: str
    output_virtual_path: str
    manifest_path: str
    manifest_virtual_path: str
    scoped_text: str
    langextract_raw: Any
    aggregated: dict[str, Any]
    cover_metadata_hint: dict[str, Any]
    validation: dict[str, Any]
    errors: Annotated[list[str], operator.add]
    warnings: Annotated[list[str], operator.add]
    status: Literal["ok", "failed"]
