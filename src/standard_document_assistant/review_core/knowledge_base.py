"""Rule knowledge base for the standard review graph.

Parses the review rules markdown into ``RuleItem`` chunks and builds (or
loads) a vector index for retrieval. The knowledge base is the single source
of truth for both the content-track LLM judge and the format-track rules.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from standard_document_assistant.config import StandardReviewConfig
from standard_document_assistant.constants import PROJECT_ROOT, REVIEW_RULES_DIR
from standard_document_assistant.review_core.retriever import (
    VectorIndex,
    build_tfidf_index,
    search_faiss_or_tfidf,
)
from standard_document_assistant.review_core.retrievers import FaissVectorRetriever
from standard_document_assistant.review_core.rule_models import QueryContext, RetrievalHit, RuleItem


_DEFAULT_RULES: list[dict[str, Any]] = [
    {
        "chunk_id": "SR-P0-001",
        "title": "标准/范围章节完整性",
        "scope": "scope",
        "content": "标准应在「范围」一章明确说明标准适用对象、适用边界和不适用范围。",
        "tags": ["范围", "完整性"],
        "analysis_mode": "local",
        "target_scopes": ["scope"],
        "source_ref": "built-in:SR-P0-001",
    },
    {
        "chunk_id": "SR-P0-002",
        "title": "规范性引用文件章节完整性",
        "scope": "normative_references",
        "content": "标准应明确列出规范性引用文件；无引用时应在章节中说明「无规范性引用文件」。",
        "tags": ["规范性引用文件", "完整性"],
        "analysis_mode": "local",
        "target_scopes": ["normative_references"],
        "source_ref": "built-in:SR-P0-002",
    },
    {
        "chunk_id": "SR-P0-003",
        "title": "术语和定义章节完整性",
        "scope": "terms_definitions",
        "content": "需要术语定义的标准应提供「术语和定义」章节；条目编号不按条编号判定。",
        "tags": ["术语", "完整性"],
        "analysis_mode": "local",
        "target_scopes": ["terms_definitions"],
        "source_ref": "built-in:SR-P0-003",
    },
    {
        "chunk_id": "SR-P0-101",
        "title": "全文结构/范围与编号一致性",
        "scope": "full_document",
        "content": "全文级结构规范：目次与正文标题一致；章/条编号连续；附录标记规范。",
        "tags": ["全文", "结构"],
        "analysis_mode": "full_document",
        "target_scopes": ["cover", "toc", "foreword", "body", "end"],
        "source_ref": "built-in:SR-P0-101",
    },
]


_OPTIONAL_SCOPES = frozenset({"toc", "introduction", "end"})


class RuleKnowledgeBase:
    """规则知识库，统一管理 ``rules`` / ``index`` (TF-IDF JSON) / FAISS 检索器。

    加载策略（``load_knowledge_base`` 主导）：

    1. 若 ``index_dir/rules.faiss`` + ``rules.faiss.meta.json`` + ``tfidf_vectorizer.pkl``
       三件套齐全且未要求 ``force_rebuild``，优先走 FAISS。
    2. 否则退到 ``index_dir/rules.faiss.json``（纯 Python TF-IDF 回退），
       保证 ``faiss-cpu`` 不可用时仍可运行。
    3. 都缺失时按 ``force_rebuild`` 决定是否重建：优先尝试 FAISS（依赖
       ``faiss-cpu`` + ``scikit-learn``），捕获 ``ImportError`` 后退到 JSON。
    """

    def __init__(
        self,
        rules: list[RuleItem],
        index: VectorIndex | None = None,
    ) -> None:
        self.rules = rules
        self.index = index
        # FAISS 检索器是按 index_dir 缓存的，避免在多次 search 时反复反序列化。
        self._faiss_retriever: dict[str, FaissVectorRetriever] = {}

    @classmethod
    def from_markdown(cls, rule_markdown_path: str | Path, *, embedding_dim: int = 1024) -> "RuleKnowledgeBase":
        path = Path(rule_markdown_path)
        if not path.exists():
            return cls(rules=[RuleItem.from_dict(item) for item in _DEFAULT_RULES])
        rules = _parse_rule_chunks(path)
        if not rules:
            rules = [RuleItem.from_dict(item) for item in _DEFAULT_RULES]
        index = build_tfidf_index(rules, dim=embedding_dim)
        return cls(rules=rules, index=index)

    @classmethod
    def from_index(cls, index_dir: str | Path) -> "RuleKnowledgeBase":
        """从 TF-IDF JSON 索引加载（保持向后兼容）。"""
        path = Path(index_dir) / "rules.faiss.json"
        if not path.exists():
            raise FileNotFoundError(f"向量索引不存在：{path}")
        index = VectorIndex.load(path.parent)
        return cls(rules=index.rules, index=index)

    @classmethod
    def from_faiss_index(cls, index_dir: str | Path) -> "RuleKnowledgeBase":
        """从 FAISS 三件套加载（``rules.faiss`` + meta + pkl）。"""
        index_path = Path(index_dir)
        retriever = FaissVectorRetriever.load(
            index_path=str(index_path / "rules.faiss"),
            metadata_path=str(index_path / "rules.faiss.meta.json"),
            vectorizer_path=str(index_path / "tfidf_vectorizer.pkl"),
        )
        kb = cls(rules=list(retriever.rules))
        kb._faiss_retriever[str(index_path.resolve())] = retriever
        # 同步构建一份轻量 VectorIndex（不带 vectors），供旧 search 路径使用。
        return kb

    def build_faiss(self, index_dir: str | Path) -> dict[str, Any]:
        """构建并持久化 FAISS 三件套到 ``index_dir``。

        依赖 ``faiss-cpu`` + ``scikit-learn``；缺包时抛 ``ImportError``，由
        :func:`load_knowledge_base` 在外层捕获并退到 JSON 回退。
        """
        index_path = Path(index_dir)
        index_path.mkdir(parents=True, exist_ok=True)

        retriever = FaissVectorRetriever(self.rules)
        retriever.build()
        retriever.save(
            index_path=str(index_path / "rules.faiss"),
            metadata_path=str(index_path / "rules.faiss.meta.json"),
            vectorizer_path=str(index_path / "tfidf_vectorizer.pkl"),
        )
        # 缓存新构建的检索器，并清掉旧实例。
        self._faiss_retriever = {
            str(index_path.resolve()): retriever,
        }
        return {
            "index_path": str(index_path / "rules.faiss"),
            "meta_path": str(index_path / "rules.faiss.meta.json"),
            "vectorizer_path": str(index_path / "tfidf_vectorizer.pkl"),
            "rules_total": len(self.rules),
        }

    def search_faiss(
        self,
        query: str,
        scope: str | None,
        top_k: int,
        index_dir: str | Path,
    ) -> list[RetrievalHit]:
        """走 FAISS 检索；首次按 index_dir 缓存，后续直接复用。"""
        retriever = self._get_faiss_retriever(index_dir)
        return retriever.search(QueryContext(query=query, scope=scope), top_k=top_k)

    def warmup_faiss(self, index_dir: str | Path) -> None:
        """预热 FAISS 检索器，避免首次 search 时被并发触发加载。"""
        self._get_faiss_retriever(index_dir)

    def _get_faiss_retriever(self, index_dir: str | Path) -> FaissVectorRetriever:
        key = str(Path(index_dir).resolve())
        cached = self._faiss_retriever.get(key)
        if cached is not None:
            return cached
        index_path = Path(key)
        retriever = FaissVectorRetriever.load(
            index_path=str(index_path / "rules.faiss"),
            metadata_path=str(index_path / "rules.faiss.meta.json"),
            vectorizer_path=str(index_path / "tfidf_vectorizer.pkl"),
        )
        self._faiss_retriever[key] = retriever
        return retriever

    def search(
        self,
        query: str,
        *,
        scope: str | None = None,
        top_k: int = 8,
        index_dir: str | Path | None = None,
    ) -> list[RetrievalHit]:
        # 优先尝试 FAISS：faiss-cpu 装好且 index_dir 下三件套齐全时走原路径。
        if index_dir is not None:
            try:
                return self.search_faiss(query, scope=scope, top_k=top_k, index_dir=index_dir)
            except (FileNotFoundError, ImportError, RuntimeError, ValueError):
                # 缺文件/缺包/索引维度异常都退到 TF-IDF，不阻塞主流程。
                pass
        if self.index is None:
            return []
        context = QueryContext(query=query, scope=scope)
        return search_faiss_or_tfidf(self.index, context, top_k=top_k, index_dir=None)

    def to_metadata(self) -> dict[str, Any]:
        return {
            "rules_total": len(self.rules),
            "scope_distribution": _scope_distribution(self.rules),
            "analysis_mode_distribution": _analysis_mode_distribution(self.rules),
        }


def load_knowledge_base(
    config: StandardReviewConfig,
    *,
    force_rebuild: bool = False,
    backend: str = "auto",
) -> tuple[RuleKnowledgeBase, dict[str, Any]]:
    """Build or load the knowledge base; return both the KB and run metadata.

    ``backend`` 决定构建/加载顺序：

    - ``"faiss"``：仅走 FAISS（缺依赖时抛 ``ImportError``）。
    - ``"tfidf_json"``：仅走 ``rules.faiss.json``（纯 Python TF-IDF）。
    - ``"auto"``（默认）：先 FAISS，缺包/缺文件时退到 JSON。
    """

    rules_path = Path(config.rules_md)
    if not rules_path.is_absolute():
        rules_path = PROJECT_ROOT / rules_path
    index_dir = Path(config.index_dir)
    if not index_dir.is_absolute():
        index_dir = PROJECT_ROOT / index_dir
    index_dir.mkdir(parents=True, exist_ok=True)

    metadata: dict[str, Any] = {
        "rules_path": str(rules_path),
        "index_dir": str(index_dir),
        "embedding_dim": int(config.embedding_dim),
        "index_backend": "unknown",
    }
    faiss_triplet = (
        index_dir / "rules.faiss",
        index_dir / "rules.faiss.meta.json",
        index_dir / "tfidf_vectorizer.pkl",
    )
    faiss_json_path = index_dir / "rules.faiss.json"

    faiss_available = backend in ("auto", "faiss")
    tfidf_available = backend in ("auto", "tfidf_json")

    # ---- 阶段 1：磁盘加载（不重建） ----
    if not force_rebuild:
        if faiss_available and all(p.exists() for p in faiss_triplet):
            try:
                kb = RuleKnowledgeBase.from_faiss_index(index_dir)
                if kb.rules and any(rule.title for rule in kb.rules):
                    metadata["rules_loaded"] = len(kb.rules)
                    metadata["index_source"] = "disk"
                    metadata["index_backend"] = "faiss"
                    return kb, metadata
            except Exception:
                pass
        if tfidf_available and faiss_json_path.exists():
            try:
                kb = RuleKnowledgeBase.from_index(index_dir)
                if kb.rules and any(rule.title for rule in kb.rules):
                    metadata["rules_loaded"] = len(kb.rules)
                    metadata["index_source"] = "disk"
                    metadata["index_backend"] = "tfidf_json"
                    return kb, metadata
            except Exception:
                pass

    # ---- 阶段 2：从 markdown 重建 ----
    kb = RuleKnowledgeBase.from_markdown(rules_path, embedding_dim=int(config.embedding_dim))

    # 优先尝试 FAISS 重建
    if faiss_available:
        try:
            kb.build_faiss(index_dir)
            metadata["rules_loaded"] = len(kb.rules)
            metadata["index_source"] = "rebuilt"
            metadata["index_backend"] = "faiss"
            metadata["rules_hash"] = _hash_rules_text(rules_path)
            return kb, metadata
        except ImportError as exc:
            metadata.setdefault("warnings", []).append(
                f"faiss-cpu / scikit-learn 不可用，回退到 tfidf_json：{exc}"
            )
            if backend == "faiss":
                # 用户明确要求 faiss，缺依赖就直接抛。
                raise
        except Exception as exc:  # pragma: no cover - 取决于 faiss 版本
            metadata.setdefault("warnings", []).append(
                f"FAISS 索引构建失败，回退到 tfidf_json：{exc}"
            )
            if backend == "faiss":
                raise

    # 退到 JSON TF-IDF 重建
    if tfidf_available:
        if kb.index is not None:
            kb.index.save(index_dir)
        metadata["rules_loaded"] = len(kb.rules)
        metadata["index_source"] = "rebuilt"
        metadata["index_backend"] = "tfidf_json"
        metadata["rules_hash"] = _hash_rules_text(rules_path)
        return kb, metadata

    raise RuntimeError(
        f"backend={backend!r} 不被支持，可选 'auto' | 'faiss' | 'tfidf_json'。"
    )


def filter_content_audit_rules(rules: list[RuleItem]) -> list[RuleItem]:
    return [rule for rule in rules if rule.scope != "format" and rule.analysis_mode != "format_only"]


def _scope_distribution(rules: list[RuleItem]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for rule in rules:
        counts[rule.scope] = counts.get(rule.scope, 0) + 1
    return counts


def _analysis_mode_distribution(rules: list[RuleItem]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for rule in rules:
        counts[rule.analysis_mode] = counts.get(rule.analysis_mode, 0) + 1
    return counts


def _hash_rules_text(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_text(encoding="utf-8", errors="ignore").encode("utf-8")).hexdigest()


def _parse_rule_chunks(path: Path) -> list[RuleItem]:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    sections = _split_rule_sections(raw)
    chunks: list[RuleItem] = []
    counter = 1
    for title, body, parent_tags in sections:
        if not body:
            continue
        paragraphs = _split_rule_paragraphs(body)
        scope = _guess_scope(title)
        for index, paragraph in enumerate(paragraphs, start=1):
            analysis_mode, target_scopes = _infer_analysis_config(
                title=title, paragraph=paragraph, default_scope=scope
            )
            tags = [scope, title]
            for tag in parent_tags:
                if tag not in tags:
                    tags.append(tag)
            chunk_id = f"RAG-{counter:04d}"
            chunks.append(
                RuleItem(
                    chunk_id=chunk_id,
                    title=title,
                    scope=scope,
                    content=paragraph,
                    source_ref=f"{path.as_posix()}#{title}-p{index}",
                    tags=tags,
                    analysis_mode=analysis_mode,
                    target_scopes=target_scopes,
                )
            )
            counter += 1
    return chunks


def _split_rule_paragraphs(body: str) -> list[str]:
    paragraphs: list[str] = []
    current: list[str] = []
    in_fence = False
    for raw_line in body.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            current.append(line)
            continue
        if not stripped and not in_fence:
            if current:
                paragraphs.append("\n".join(current).strip())
                current = []
            continue
        current.append(line)
    if current:
        paragraphs.append("\n".join(current).strip())
    return [p for p in paragraphs if p]


def _split_rule_sections(raw: str) -> list[tuple[str, str, list[str]]]:
    sections: list[tuple[str, str, list[str]]] = []
    current_h1: str | None = None
    current_title: str | None = None
    current_lines: list[str] = []
    current_tags: list[str] = []

    def flush() -> None:
        nonlocal current_title, current_lines, current_tags
        if current_title is None:
            return
        body = "\n".join(current_lines).strip()
        if body:
            sections.append((current_title, body, list(current_tags)))
        current_title = None
        current_lines = []
        current_tags = []

    for line in raw.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not match:
            if current_title is not None:
                current_lines.append(line)
            continue
        level = len(match.group(1))
        heading_text = match.group(2).strip()
        if level == 1:
            flush()
            current_h1 = heading_text
            current_title = heading_text
            current_tags = [heading_text]
            continue
        if level == 2:
            flush()
            if current_h1 in {"正文", "格式规范"}:
                current_title = f"{current_h1}/{heading_text}"
                current_tags = [current_h1, heading_text]
            else:
                current_title = heading_text
                current_tags = [current_h1 or heading_text, heading_text]
            continue
        if current_title is not None:
            current_lines.append(line)
    flush()
    return sections


def _guess_scope(title: str) -> str:
    normalized = title.replace(" ", "")
    if "全文" in normalized:
        return "full_document"
    if "格式规范" in normalized:
        return "format"
    if "封面" in normalized:
        return "cover"
    if "目次" in normalized:
        return "toc"
    if "前言" in normalized:
        return "foreword"
    if "引言" in normalized:
        return "introduction"
    if "规范性引用文件" in normalized:
        return "normative_references"
    if "术语和定义" in normalized:
        return "terms_definitions"
    if "符号和缩略语" in normalized or "符号缩略语" in normalized or "代号和缩略语" in normalized:
        return "symbols_abbreviations"
    if "范围" in normalized:
        return "scope"
    if "附录" in normalized:
        return "appendix"
    if "参考文献" in normalized:
        return "references"
    if "索引" in normalized:
        return "index"
    if "正文" in normalized:
        return "body"
    return "other_body"


def _infer_analysis_config(*, title: str, paragraph: str, default_scope: str) -> tuple[str, list[str]]:
    title_lc = title.replace(" ", "").lower()
    if "全文" in title_lc or default_scope == "full_document":
        return "full_document", ["cover", "toc", "foreword", "body", "end"]
    if "格式" in title_lc or default_scope == "format":
        return "deterministic", []
    target = [default_scope] if default_scope else []
    if "跨" in title_lc or "整体" in title_lc:
        return "cross_section", ["scope", "normative_references", "terms_definitions", "other_body"]
    return "local", target
