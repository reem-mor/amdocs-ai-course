"""FAISS-backed dense retriever — loads the index and returns top-K relevant incident chunks.

The retriever is exposed as a process-wide singleton initialised via
`init_retriever(index_path)` at application startup. Embeddings are produced by
the shared `EmbeddingModel`, so the FAISS index must have been built with the
same sentence-transformer model.
"""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any

import faiss
import numpy as np

from app.core.embedder import get_embedding_model
from app.utils.logger import get_logger

_logger = get_logger(__name__)

_L2_DISTANCE_THRESHOLD: float = 1.5


class FAISSRetriever:
    """Loads a persisted FAISS index + metadata sidecar and runs top-K searches.

    The index is `IndexFlatL2` over L2-normalised sentence-transformer
    embeddings, so L2 distance lies in `[0, 2]` and can be converted to a
    bounded `(0, 1]` similarity score for downstream consumers.
    """

    def __init__(self, index_path: str | Path) -> None:
        """Load `index.faiss` and `metadata.json` from `index_path`.

        Args:
            index_path: Directory containing the persisted FAISS index files.

        Raises:
            FileNotFoundError: If either `index.faiss` or `metadata.json` is
                missing from the directory.
            ValueError: If the metadata length does not match the index ntotal,
                or if the file content is malformed.
        """
        self._index_path: Path = Path(index_path)
        index_file: Path = self._index_path / "index.faiss"
        metadata_file: Path = self._index_path / "metadata.json"

        if not index_file.is_file():
            raise FileNotFoundError(
                f"FAISS index file not found: {index_file}. "
                "Run scripts/build_knowledge_base.py first."
            )
        if not metadata_file.is_file():
            raise FileNotFoundError(
                f"FAISS metadata file not found: {metadata_file}. "
                "Run scripts/build_knowledge_base.py first."
            )

        self._index: faiss.Index = faiss.read_index(str(index_file))
        with metadata_file.open("r", encoding="utf-8") as fh:
            self._metadata: list[dict[str, Any]] = json.load(fh)

        if self._index.ntotal != len(self._metadata):
            raise ValueError(
                "FAISS index/metadata size mismatch: "
                f"index.ntotal={self._index.ntotal} metadata_len={len(self._metadata)}"
            )

        self._dimension: int = int(self._index.d)
        self._index_type: str = type(self._index).__name__
        _logger.info(
            "FAISS index loaded: path=%s type=%s vectors=%d dim=%d",
            self._index_path,
            self._index_type,
            self._index.ntotal,
            self._dimension,
        )

    def retrieve(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Return the top-K most similar chunks for `query`.

        Args:
            query: Free-text user query. Embedded via the shared embedding model.
            top_k: Maximum number of results to return after thresholding.

        Returns:
            A list of result dicts sorted by ascending L2 distance (most
            relevant first). Returns an empty list if no candidates fall within
            the L2 distance threshold (1.5).
        """
        if top_k <= 0:
            raise ValueError(f"top_k must be a positive integer, got {top_k}")

        embedder = get_embedding_model()
        query_vec: np.ndarray = embedder.embed_query(query)

        search_k: int = min(top_k, self._index.ntotal)
        if search_k == 0:
            _logger.info(
                "Retrieval skipped (empty index): query=%r", _truncate_for_log(query)
            )
            return []

        distances, indices = self._index.search(query_vec, search_k)
        distances_row: np.ndarray = distances[0]
        indices_row: np.ndarray = indices[0]

        results: list[dict[str, Any]] = []
        for rank_zero, (faiss_idx, distance) in enumerate(
            zip(indices_row.tolist(), distances_row.tolist())
        ):
            if faiss_idx < 0:
                continue
            if distance > _L2_DISTANCE_THRESHOLD:
                continue

            meta: dict[str, Any] = self._metadata[faiss_idx]
            results.append(
                {
                    "chunk_text": meta.get("chunk_text", ""),
                    "score": _distance_to_score(distance),
                    "rank": rank_zero + 1,
                    "metadata": {
                        "id": meta.get("id", ""),
                        "title": meta.get("title", ""),
                        "severity": meta.get("severity", "N/A"),
                        "category": meta.get("category", "SOP"),
                        "tags": list(meta.get("tags", [])),
                        "document_type": meta.get("document_type", "unknown"),
                    },
                }
            )

        for new_rank, item in enumerate(results, start=1):
            item["rank"] = new_rank

        top_score: float = results[0]["score"] if results else 0.0
        _logger.info(
            "Retrieval: query=%r results=%d top_score=%.3f",
            _truncate_for_log(query),
            len(results),
            top_score,
        )
        return results

    def get_index_stats(self) -> dict[str, Any]:
        """Return basic statistics about the loaded FAISS index."""
        return {
            "total_vectors": int(self._index.ntotal),
            "dimension": self._dimension,
            "index_type": self._index_type,
        }


def _distance_to_score(distance: float) -> float:
    """Convert an L2 distance to a bounded `(0, 1]` relevance score.

    With L2-normalised embeddings, distance lies in `[0, 2]`. The transform
    `1 / (1 + d)` maps `d=0 -> 1.0` (perfect match) and `d=2 -> 0.333` while
    preserving the ranking induced by L2 distance.
    """
    return float(1.0 / (1.0 + max(distance, 0.0)))


def _truncate_for_log(text: str, limit: int = 50) -> str:
    """Truncate `text` for log output without leaking long query payloads."""
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


_retriever_instance: FAISSRetriever | None = None
_retriever_lock: Lock = Lock()


def get_retriever() -> FAISSRetriever:
    """Return singleton retriever instance.

    Raises:
        RuntimeError: If `init_retriever` has not yet been called.
    """
    global _retriever_instance
    if _retriever_instance is None:
        raise RuntimeError(
            "Retriever not initialized. Call init_retriever() first."
        )
    return _retriever_instance


def init_retriever(index_path: str | Path) -> FAISSRetriever:
    """Initialize and return the singleton retriever.

    Subsequent calls replace the existing instance, which makes this safe to
    call from application startup hooks and from tests that need a fresh index.
    """
    global _retriever_instance
    with _retriever_lock:
        _retriever_instance = FAISSRetriever(index_path)
    return _retriever_instance
