from __future__ import annotations

import numpy as np
from langchain_core.embeddings import Embeddings
from sklearn.feature_extraction.text import HashingVectorizer


class HashingEmbeddings(Embeddings):
    """Stateless local embeddings suitable for a small Korean demo corpus."""

    def __init__(self, dimensions: int = 768):
        self.dimensions = dimensions
        self.vectorizer = HashingVectorizer(
            n_features=dimensions,
            alternate_sign=False,
            analyzer="char_wb",
            ngram_range=(2, 5),
            norm="l2",
        )

    def _embed(self, texts: list[str]) -> list[list[float]]:
        matrix = self.vectorizer.transform(texts)
        return matrix.astype(np.float32).toarray().tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text])[0]
