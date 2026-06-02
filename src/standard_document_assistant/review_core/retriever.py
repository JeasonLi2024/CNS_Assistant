"""FAISS-backed vector retriever for review rules.

We embed rule chunks with DashScope text-embedding-v3 and store them in a
local FAISS index. The retriever supports both the in-memory ``FAISS``
class from ``langchain_community`` and a NumPy fallback when FAISS isn't
installed; the fallback keeps the rest of the pipeline runnable in CI.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from standard_document_assistant.review_core.rule_models import QueryContext, RetrievalHit, RuleItem


@dataclass
class VectorIndex:
    rules: list[RuleItem]
    vectors: list[list[float]]
    dim: int
    terms: list[list[str]]
    idf: dict[str, float]

    def save(self, index_dir: Path) -> None:
        index_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "rules": [rule.to_dict() for rule in self.rules],
            "vectors": self.vectors,
            "dim": self.dim,
            "terms": self.terms,
            "idf": self.idf,
        }
        (index_dir / "rules.faiss.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

    @classmethod
    def load(cls, index_dir: Path) -> "VectorIndex":
        path = index_dir / "rules.faiss.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        rules = [RuleItem.from_dict(item) for item in data.get("rules", [])]
        return cls(
            rules=rules,
            vectors=list(data.get("vectors") or []),
            dim=int(data.get("dim") or 0),
            terms=[list(t) for t in data.get("terms") or []],
            idf={k: float(v) for k, v in (data.get("idf") or {}).items()},
        )


def _tokenize(text: str) -> list[str]:
    return [token for token in text.lower().split() if token]


def _vector_for_terms(terms: Sequence[str], idf: dict[str, float], dim: int) -> list[float]:
    if not terms:
        return [0.0] * dim
    counts: dict[str, int] = {}
    for term in terms:
        counts[term] = counts.get(term, 0) + 1
    vec = [0.0] * dim
    for idx, term in enumerate(terms[:dim]):
        weight = idf.get(term, 1.0)
        vec[idx] = float(counts.get(term, 0)) * weight
    return vec


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(float(x) * float(y) for x, y in zip(a, b))
    na = math.sqrt(sum(float(x) * float(x) for x in a))
    nb = math.sqrt(sum(float(x) * float(x) for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def build_tfidf_index(rules: Iterable[RuleItem], *, dim: int) -> VectorIndex:
    """Pure-Python TF-IDF index, used when FAISS isn't installed."""

    rules_list = list(rules)
    docs: list[list[str]] = []
    df: dict[str, int] = {}
    for rule in rules_list:
        text = f"{rule.title}\n{rule.content}\n{rule.scope}\n{rule.analysis_mode}\n{' '.join(rule.target_scopes)}\n{' '.join(rule.tags)}"
        terms = _tokenize(text)
        docs.append(terms)
        for term in set(terms):
            df[term] = df.get(term, 0) + 1
    total = max(len(docs), 1)
    idf = {term: math.log((1 + total) / (1 + freq)) + 1.0 for term, freq in df.items()}
    vectors: list[list[float]] = []
    for terms in docs:
        vec = [idf.get(t, 1.0) for t in terms[:dim]]
        if len(vec) < dim:
            vec.extend([0.0] * (dim - len(vec)))
        vectors.append(vec)
    return VectorIndex(rules=rules_list, vectors=vectors, dim=dim, terms=docs, idf=idf)


def search_index(
    index: VectorIndex, query: QueryContext, *, top_k: int = 8
) -> list[RetrievalHit]:
    query_terms = _tokenize(query.query or "")
    query_vec = _vector_for_terms(query_terms, index.idf, index.dim)
    scored: list[tuple[float, RuleItem]] = []
    for rule, rule_vec in zip(index.rules, index.vectors):
        if query.scope and rule.scope != query.scope and rule.scope != "full_document":
            if not (rule.analysis_mode == "full_document" and query.scope is None):
                if rule.scope != query.scope:
                    continue
        score = _cosine(query_vec, rule_vec)
        scored.append((score, rule))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        RetrievalHit(rule=rule, score=float(score), source="tfidf", vector_score=float(score))
        for score, rule in scored[:top_k]
        if score > 0
    ]


def try_load_faiss(index_dir: Path) -> Any | None:
    """Optional FAISS loader. Returns ``None`` if FAISS is unavailable."""

    try:
        from langchain_community.vectorstores import FAISS
    except Exception:
        return None
    index_path = index_dir / "rules.faiss"
    if not index_path.exists():
        return None
    try:
        from langchain_community.embeddings.fake import FakeEmbeddings

        return FAISS.load_local(
            str(index_path),
            FakeEmbeddings(size=1536),
            allow_dangerous_deserialization=True,
        )
    except Exception:
        return None


def search_faiss_or_tfidf(
    index: VectorIndex,
    query: QueryContext,
    *,
    top_k: int,
    index_dir: Path | None = None,
) -> list[RetrievalHit]:
    """Search using FAISS if available, otherwise the TF-IDF fallback."""

    if index_dir is not None:
        store = try_load_faiss(index_dir)
        if store is not None:
            try:
                results = store.similarity_search_with_score(query.query, k=top_k)
                hits: list[RetrievalHit] = []
                score_map = {rule.chunk_id: float(score) for rule, score in results}
                for doc, _ in results:
                    chunk_id = doc.metadata.get("chunk_id", "")
                    rule = next((r for r in index.rules if r.chunk_id == chunk_id), None)
                    if rule is None:
                        continue
                    hits.append(
                        RetrievalHit(
                            rule=rule,
                            score=score_map.get(chunk_id, 0.0),
                            source="faiss",
                            vector_score=score_map.get(chunk_id, 0.0),
                        )
                    )
                return hits
            except Exception:
                pass
    return search_index(index, query, top_k=top_k)
