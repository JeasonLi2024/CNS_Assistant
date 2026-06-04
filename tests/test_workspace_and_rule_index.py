import importlib
import os
import time

from standard_document_assistant.config import StandardReviewConfig
from standard_document_assistant.review_core.knowledge_base import load_knowledge_base
from standard_document_assistant.review_core.retriever import VectorIndex
from standard_document_assistant.review_core.rule_models import RuleItem


def test_workspace_root_env_override(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("STANDARD_DOC_WORKSPACE_ROOT", str(tmp_path / "custom-workspace"))
    import standard_document_assistant.constants as constants

    reloaded = importlib.reload(constants)
    try:
        assert reloaded.WORKSPACE_ROOT == (tmp_path / "custom-workspace").resolve()
        assert reloaded.UPLOADS_DIR == reloaded.WORKSPACE_ROOT / "input" / "uploads"
    finally:
        monkeypatch.delenv("STANDARD_DOC_WORKSPACE_ROOT", raising=False)
        importlib.reload(constants)


def test_stale_tfidf_json_index_is_rebuilt_from_rules_markdown(tmp_path) -> None:
    rules_md = tmp_path / "rules_test.md"
    index_dir = tmp_path / "index"
    index_dir.mkdir()
    rules_md.write_text(
        "# 前言\n\n"
        "1. 【必须含有】文件起草所依据的标准。\n",
        encoding="utf-8",
    )

    stale_rule = RuleItem(
        chunk_id="OLD-0001",
        title="范围",
        scope="scope",
        content="旧范围规则",
        source_ref="old",
        tags=["scope"],
        analysis_mode="local",
        target_scopes=["scope"],
    )
    VectorIndex(rules=[stale_rule], vectors=[[1.0]], dim=1, terms=[["scope"]], idf={"scope": 1.0}).save(
        index_dir
    )
    old_time = time.time() - 60
    os.utime(index_dir / "rules.faiss.json", (old_time, old_time))
    new_time = time.time()
    os.utime(rules_md, (new_time, new_time))

    config = StandardReviewConfig(
        rules_md=str(rules_md),
        index_dir=str(index_dir),
    )
    kb, metadata = load_knowledge_base(config, backend="tfidf_json")

    assert metadata["index_source"] == "rebuilt"
    assert metadata["index_backend"] == "tfidf_json"
    assert any(rule.scope == "foreword" for rule in kb.rules)


def test_unwritable_tfidf_json_index_uses_memory_index(monkeypatch, tmp_path) -> None:
    rules_md = tmp_path / "rules_test.md"
    index_dir = tmp_path / "index"
    index_dir.mkdir()
    rules_md.write_text(
        "# 前言\n\n"
        "1. 【必须含有】文件起草所依据的标准。\n",
        encoding="utf-8",
    )

    def fail_save(self, index_dir):
        raise PermissionError("index is read-only")

    monkeypatch.setattr(VectorIndex, "save", fail_save)
    config = StandardReviewConfig(
        rules_md=str(rules_md),
        index_dir=str(index_dir),
    )
    kb, metadata = load_knowledge_base(config, backend="tfidf_json", force_rebuild=True)

    assert metadata["index_source"] == "memory"
    assert metadata["index_backend"] == "tfidf_memory"
    assert any(rule.scope == "foreword" for rule in kb.rules)
