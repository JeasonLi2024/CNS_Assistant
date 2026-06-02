"""Business tools exposed to Deep Agents."""

from standard_document_assistant.tools.metadata import extract_standard_metadata
from standard_document_assistant.tools.parser import parse_document_with_mineru, parse_file_with_mineru
from standard_document_assistant.tools.review import (
    build_review_index,
    inspect_review_rules,
    run_format_source_review,
    run_standard_review,
    validate_review_result_schema,
)
from standard_document_assistant.tools.validation import (
    propose_memory_update,
    validate_output_schema,
)

PARSER_TOOLS = [parse_file_with_mineru]
METADATA_TOOLS = [extract_standard_metadata, validate_output_schema]
REVIEW_TOOLS = [
    parse_document_with_mineru,
    run_standard_review,
    run_format_source_review,
    inspect_review_rules,
    build_review_index,
    validate_review_result_schema,
]
STANDARD_DOCUMENT_TOOLS = [
    validate_output_schema,
    propose_memory_update,
]

__all__ = [
    "PARSER_TOOLS",
    "METADATA_TOOLS",
    "REVIEW_TOOLS",
    "STANDARD_DOCUMENT_TOOLS",
    "build_review_index",
    "extract_standard_metadata",
    "inspect_review_rules",
    "parse_document_with_mineru",
    "parse_file_with_mineru",
    "propose_memory_update",
    "run_format_source_review",
    "run_standard_review",
    "validate_output_schema",
    "validate_review_result_schema",
]
