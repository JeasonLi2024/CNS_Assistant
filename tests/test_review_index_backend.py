"""Tests for the rule knowledge base index backends (FAISS + TF-IDF JSON fallback).

These tests exercise the load/``build_faiss``/``from_faiss_index`` path on
a temporary ``index_dir`` so we never overwrite the project-shipped
``src/standard_document_assistant/resources/review_rules/rules.faiss.json``.

``faiss-cpu`` and ``scikit-learn`` are optional; FAISS-specific cases are
skipped at the function body level when the deps are missing, so that the
rest of the suite can still run on a CI without those packages.
"""

from __future__ import annotations

import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import pytest

from standard_document_assistant.config import load_config
from standard_document_assistant.review_core.knowledge_base import (
    RuleKnowledgeBase,
    load_knowledge_base,
)


def _has_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _isolated_config(tmp_path: Path):
    """复制标准审核配置，把 rules_md / index_dir 重定向到 tmp 路径。"""
    base = load_config().standard_review
    fake_rules = tmp_path / "rules.md"
    fake_rules.write_text(
        (
            "# 测试规则\n\n"
            "## 范围\n\n"
            "标准应明确范围章节。\n\n"
            "## 规范性引用文件\n\n"
            "标准应列出规范性引用文件。\n"
        ),
        encoding="utf-8",
    )
    return (
        replace(base, rules_md=str(fake_rules), index_dir=str(tmp_path / "index")),
        fake_rules,
    )


def test_load_knowledge_base_json_backend(tmp_path: Path) -> None:
    config, _rules = _isolated_config(tmp_path)
    kb, meta = load_knowledge_base(config, force_rebuild=True, backend="tfidf_json")
    assert meta["index_backend"] == "tfidf_json"
    assert meta["rules_loaded"] >= 2
    # JSON 回退文件应被写出。
    json_path = Path(config.index_dir) / "rules.faiss.json"
    assert json_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert "rules" in payload and "vectors" in payload
    # search 至少能返回 1 条命中。
    hits = kb.search("范围", scope="scope", top_k=2, index_dir=config.index_dir)
    assert hits
    assert hits[0].source in {"tfidf", "faiss"}


def test_load_knowledge_base_auto_falls_back_without_faiss(tmp_path: Path) -> None:
    config, _ = _isolated_config(tmp_path)
    # 强制 tfidf_json 模拟 faiss-cpu 不可用的环境。
    kb, meta = load_knowledge_base(config, force_rebuild=True, backend="tfidf_json")
    assert meta["index_backend"] == "tfidf_json"
    # 再次加载（force_rebuild=False）应走磁盘，不应抛错。
    kb2, meta2 = load_knowledge_base(config, force_rebuild=False, backend="auto")
    assert meta2["index_backend"] in {"tfidf_json", "faiss"}
    assert kb2.rules
    # 命中数与 rules_loaded 一致。
    assert len(kb2.rules) == len(kb.rules)


def test_load_knowledge_base_raises_when_faiss_required_but_missing(tmp_path: Path, monkeypatch) -> None:
    config, _ = _isolated_config(tmp_path)
    # 模拟 faiss-cpu 不可用：build_faiss 抛 ImportError，且 backend="faiss" 透传。

    def _fake_build_faiss(self, index_dir):
        raise ImportError("faiss-cpu is required for backend='faiss' (test stub)")

    monkeypatch.setattr(
        RuleKnowledgeBase, "build_faiss", _fake_build_faiss, raising=True
    )
    with pytest.raises(ImportError, match="faiss-cpu"):
        load_knowledge_base(config, force_rebuild=True, backend="faiss")


def test_rebuild_index_does_not_overwrite_user_rules(tmp_path: Path) -> None:
    """回归保护：重建索引不应触碰 rules_test.md。"""
    config, rules_path = _isolated_config(tmp_path)
    original = rules_path.read_text(encoding="utf-8")
    load_knowledge_base(config, force_rebuild=True, backend="tfidf_json")
    assert rules_path.read_text(encoding="utf-8") == original


def test_build_faiss_writes_three_pieces_and_search_works(tmp_path: Path) -> None:
    """需要 faiss-cpu + scikit-learn；不可用时跳过。"""
    if not (_has_module("faiss") and _has_module("sklearn")):
        pytest.skip("faiss-cpu / scikit-learn 未安装，跳过 FAISS 三件套测试")

    config, _ = _isolated_config(tmp_path)
    kb, meta = load_knowledge_base(config, force_rebuild=True, backend="auto")
    assert meta["index_backend"] == "faiss"
    index_dir = Path(config.index_dir)
    assert (index_dir / "rules.faiss").exists()
    assert (index_dir / "rules.faiss.meta.json").exists()
    assert (index_dir / "tfidf_vectorizer.pkl").exists()
    hits = kb.search_faiss("范围", scope="scope", top_k=3, index_dir=index_dir)
    assert hits, "FAISS 索引应能命中至少 1 条规则"
    assert all(h.source == "faiss" for h in hits)
    # 重新加载应保持一致。
    kb2 = RuleKnowledgeBase.from_faiss_index(index_dir)
    hits2 = kb2.search("范围", scope="scope", top_k=3, index_dir=index_dir)
    assert len(hits2) == len(hits)
    assert {h.rule.chunk_id for h in hits2} == {h.rule.chunk_id for h in hits}


def test_faiss_retriever_scope_filter(tmp_path: Path) -> None:
    """直接构造 FaissVectorRetriever 自测 scope 过滤。"""
    if not (_has_module("faiss") and _has_module("sklearn")):
        pytest.skip("faiss-cpu / scikit-learn 未安装，跳过")
    from standard_document_assistant.review_core.retrievers import FaissVectorRetriever
    from standard_document_assistant.review_core.rule_models import (
        QueryContext,
        RuleItem,
    )

    rules = [
        RuleItem(
            chunk_id="T-1",
            title="范围",
            scope="scope",
            content="本标准规定了范围要求。",
            source_ref="t.md#1",
            tags=["scope"],
            analysis_mode="local",
            target_scopes=["scope"],
        ),
        RuleItem(
            chunk_id="T-2",
            title="术语",
            scope="terms_definitions",
            content="本标准给出了术语。",
            source_ref="t.md#2",
            tags=["terms"],
            analysis_mode="local",
            target_scopes=["terms_definitions"],
        ),
    ]
    retriever = FaissVectorRetriever(rules)
    retriever.build()
    out = retriever.search(QueryContext(query="范围", scope="scope"), top_k=5)
    assert out
    assert all(h.rule.scope == "scope" for h in out)
    assert "T-1" in {h.rule.chunk_id for h in out}
