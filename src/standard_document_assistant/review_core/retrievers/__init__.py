"""Retrievers for the rule knowledge base.

- :class:`VectorizerStore`：scikit-learn ``TfidfVectorizer`` 拟合/序列化封装。
- :class:`FaissVectorRetriever`：基于 ``faiss-cpu`` 的稠密向量检索（IndexFlatIP + L2 归一化）。
- 索引产物三件套：``rules.faiss``、``rules.faiss.meta.json``、``tfidf_vectorizer.pkl``。
"""

from .vector_retriever import FaissVectorRetriever, VectorizerStore

__all__ = ["FaissVectorRetriever", "VectorizerStore"]
