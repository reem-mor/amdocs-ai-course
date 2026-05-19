"""Sentence-transformer embedder — encodes text into dense vectors for FAISS indexing/search.

Loads the underlying `SentenceTransformer` model exactly once per process via a
module-level singleton, so embedding remains cheap on subsequent calls.
Embeddings are L2-normalised so that L2 distance is bounded in `[0, 2]` and can
be used interchangeably with cosine similarity in downstream retrieval logic.
"""

from __future__ import annotations

import time
from threading import Lock

import numpy as np
from sentence_transformers import SentenceTransformer

from app.config import get_settings
from app.utils.logger import get_logger

_logger = get_logger(__name__)

_singleton_instance: "EmbeddingModel | None" = None
_singleton_lock: Lock = Lock()


class EmbeddingModel:
    """Process-wide wrapper around a `SentenceTransformer` model.

    The model is loaded on first instantiation and reused for the lifetime of
    the process. Public callers should always obtain the instance via
    `get_embedding_model()` rather than constructing this class directly.
    """

    def __init__(self) -> None:
        """Load the configured sentence-transformer model and log timing.

        Raises:
            OSError: If the model cannot be downloaded or read from cache.
            RuntimeError: If the model fails to initialise on the current device.
        """
        settings = get_settings()
        self._model_name: str = settings.EMBEDDING_MODEL

        _logger.info("Loading embedding model: %s", self._model_name)
        start: float = time.perf_counter()
        self._model: SentenceTransformer = SentenceTransformer(self._model_name)
        elapsed: float = time.perf_counter() - start
        self._dimension: int
        if hasattr(self._model, "get_embedding_dimension"):
            self._dimension = int(self._model.get_embedding_dimension())
        else:
            self._dimension = int(self._model.get_sentence_embedding_dimension())
        _logger.info(
            "Embedding model loaded: name=%s dim=%d elapsed=%.2fs",
            self._model_name,
            self._dimension,
            elapsed,
        )

    @property
    def model_name(self) -> str:
        """Return the underlying sentence-transformer model identifier."""
        return self._model_name

    @property
    def dimension(self) -> int:
        """Return the embedding vector dimensionality."""
        return self._dimension

    def embed(self, texts: list[str]) -> np.ndarray:
        """Encode a list of texts into a 2-D `float32` numpy array.

        Args:
            texts: Documents/chunks to embed. May be empty.

        Returns:
            Array of shape `(len(texts), dimension)` with dtype `float32`. When
            `texts` is empty, returns an empty `(0, dimension)` array.
        """
        if not texts:
            return np.empty((0, self._dimension), dtype=np.float32)

        vectors = self._model.encode(
            texts,
            batch_size=32,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return np.asarray(vectors, dtype=np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        """Encode a single query string into a 2-D array of shape `(1, dim)`.

        Args:
            text: Query string. Leading/trailing whitespace is stripped.

        Returns:
            `float32` array of shape `(1, dimension)`.

        Raises:
            ValueError: If `text` is empty after stripping whitespace.
        """
        stripped = text.strip()
        if not stripped:
            raise ValueError("Query text must be non-empty after stripping whitespace.")
        return self.embed([stripped])


def get_embedding_model() -> EmbeddingModel:
    """Return the singleton embedding model instance.

    Instantiation is thread-safe via a double-checked lock; the underlying
    sentence-transformer model is loaded exactly once per process.
    """
    global _singleton_instance
    if _singleton_instance is None:
        with _singleton_lock:
            if _singleton_instance is None:
                _singleton_instance = EmbeddingModel()
    return _singleton_instance
