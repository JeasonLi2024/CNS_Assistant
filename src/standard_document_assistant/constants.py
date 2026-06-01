"""Project paths and constants."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = PROJECT_ROOT / "workspace"
INPUT_DIR = WORKSPACE_ROOT / "input"
UPLOADS_DIR = INPUT_DIR / "uploads"
SAMPLES_DIR = INPUT_DIR / "samples"
OUTPUT_DIR = WORKSPACE_ROOT / "output"
TMP_DIR = WORKSPACE_ROOT / "tmp"
TEMPLATES_DIR = WORKSPACE_ROOT / "templates"
MINERU_OUTPUT_DIR = OUTPUT_DIR / "mineru"
METADATA_OUTPUT_DIR = OUTPUT_DIR / "metadata"
REVIEWS_OUTPUT_DIR = OUTPUT_DIR / "reviews"
REPORTS_OUTPUT_DIR = OUTPUT_DIR / "reports"
DRAFTS_OUTPUT_DIR = OUTPUT_DIR / "drafts"
ARTIFACTS_DIR = OUTPUT_DIR / "artifacts"
MEMORIES_DIR = PROJECT_ROOT / "memories"
SKILLS_DIR = PROJECT_ROOT / "skills"
SUBAGENTS_DIR = PROJECT_ROOT / "subagents"
REVIEW_RULES_DIR = PROJECT_ROOT / "src" / "standard_document_assistant" / "resources" / "review_rules"

AGENT_NAME = "standard-document-assistant"
MEMORY_NAMESPACE = (AGENT_NAME, "memories")
