"""基于 scikit-learn TfidfVectorizer + faiss-cpu 的稠密向量检索器。

设计要点
--------

- 训练期：``VectorizerStore.fit_transform`` 把所有 ``RuleItem.retrieval_text()``
  拼成 TF-IDF 矩阵，``faiss.IndexFlatIP`` 接收 L2 归一化后的稠密向量（即
  内积等价余弦）。
- 持久化：``save`` 写三件套到 ``index_dir``：
  - ``rules.faiss``：faiss 二进制索引（``faiss.write_index``）。
  - ``rules.faiss.meta.json``：``chunk_id_map`` + 全部 ``RuleItem.to_dict()``。
  - ``tfidf_vectorizer.pkl``：pickle 序列化后的 ``TfidfVectorizer``。
- 检索期：``load`` 一次性反序列化三件套，``search`` 时只对 query 做
  ``vectorizer.transform`` + L2 归一化 + ``index.search``，再用
  ``QueryContext.scope`` 过滤 scope 不匹配的规则。
- 可选依赖：缺 ``faiss-cpu`` 时 ``build()`` / ``load()`` 抛 ``ImportError``，
  由 :mod:`standard_document_assistant.review_core.knowledge_base` 在更外层
  捕获并退到 ``rules.faiss.json``（纯 Python TF-IDF 回退）。
"""

from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import List, Optional

import numpy as np

from standard_document_assistant.review_core.rule_models import (
    QueryContext,
    RetrievalHit,
    RuleItem,
)


class VectorizerStore:
    """TF-IDF 向量化器，封装 scikit-learn 拟合、序列化、复用。"""

    def __init__(self, vectorizer: Optional["object"] = None) -> None:
        # 延迟导入 scikit-learn，避免 review_core 顶层强依赖。
        from sklearn.feature_extraction.text import TfidfVectorizer  # noqa: F401

        self.vectorizer = vectorizer or TfidfVectorizer(token_pattern=r"(?u)\b\w+\b")

    def fit_transform(self, texts: List[str]) -> np.ndarray:
        matrix = self.vectorizer.fit_transform(texts)
        dense = matrix.astype(np.float32).toarray()
        return _l2_normalize(dense)

    def transform(self, text: str) -> np.ndarray:
        matrix = self.vectorizer.transform([text])
        dense = matrix.astype(np.float32).toarray()
        return _l2_normalize(dense)

    def save(self, path: str) -> None:
        with Path(path).open("wb") as f:
            pickle.dump(self.vectorizer, f)

    @classmethod
    def load(cls, path: str) -> "VectorizerStore":
        with Path(path).open("rb") as f:
            vectorizer = pickle.load(f)
        return cls(vectorizer=vectorizer)


class FaissVectorRetriever:
    """faiss-cpu 稠密向量检索器，配套 VectorizerStore 使用。"""

    def __init__(
        self,
        rules: List[RuleItem],
        vectorizer_store: Optional[VectorizerStore] = None,
    ) -> None:
        self.rules: List[RuleItem] = list(rules)
        self.vectorizer_store = vectorizer_store or VectorizerStore()
        self.index: Optional["object"] = None
        self._chunk_id_map: List[str] = []

    def build(self) -> None:
        try:
            import faiss
        except ImportError as exc:  # pragma: no cover - 取决于可选依赖
            raise ImportError(
                "faiss-cpu is required for backend='faiss'. "
                "请先执行 `pip install faiss-cpu` 或改用 tfidf_json 后端。"
            ) from exc

        vectors = self.vectorizer_store.fit_transform(
            [r.retrieval_text() for r in self.rules]
        )
        if vectors.shape[0] == 0:
            # 0 条规则时建空索引，search 会直接返回空。
            self.index = faiss.IndexFlatIP(1)
            self._chunk_id_map = []
            return
        self.index = faiss.IndexFlatIP(vectors.shape[1])
        self.index.add(vectors)  # type: ignore[arg-type]
        self._chunk_id_map = [r.chunk_id for r in self.rules]

    def save(
        self,
        index_path: str,
        metadata_path: str,
        vectorizer_path: str,
    ) -> None:
        if self.index is None:
            raise RuntimeError("FAISS index is not built.")
        import faiss

        faiss.write_index(self.index, index_path)
        meta = {
            "chunk_id_map": self._chunk_id_map,
            "rules": [rule.to_dict() for rule in self.rules],
        }
        Path(metadata_path).write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.vectorizer_store.save(vectorizer_path)

    @classmethod
    def load(
        cls,
        index_path: str,
        metadata_path: str,
        vectorizer_path: str,
    ) -> "FaissVectorRetriever":
        import faiss

        meta = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
        rules = [RuleItem.from_dict(x) for x in meta.get("rules", [])]
        retriever = cls(
            rules=rules,
            vectorizer_store=VectorizerStore.load(vectorizer_path),
        )
        retriever.index = faiss.read_index(index_path)
        retriever._chunk_id_map = list(meta.get("chunk_id_map") or [])
        return retriever

    def search(
        self,
        context: QueryContext,
        top_k: int = 8,
    ) -> List[RetrievalHit]:
        if self.index is None:
            raise RuntimeError("FAISS index is not built.")

        query_vec = self.vectorizer_store.transform(context.query)
        # faiss 对空查询/空索引返回空数组；做防御性长度对齐。
        try:
            k = max(int(top_k) * 3, 1)
            scores, indices = self.index.search(query_vec, k)
        except Exception:
            return []
        if not hasattr(scores, "__len__") or not hasattr(indices, "__len__"):
            return []

        hits: List[RetrievalHit] = []
        for score, idx in zip(scores[0] if len(scores) else [], indices[0] if len(indices) else []):
            i = int(idx)
            if i < 0 or i >= len(self.rules):
                continue
            rule = self.rules[i]
            if not isinstance(rule, RuleItem):
                continue
            if context.scope and rule.scope != context.scope:
                continue
            hits.append(
                RetrievalHit(
                    rule=rule,
                    score=float(score),
                    source="faiss",
                    vector_score=float(score),
                )
            )
            if len(hits) >= top_k:
                break
        return hits


def _l2_normalize(arr: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return arr / norms
