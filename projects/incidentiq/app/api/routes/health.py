"""GET /health endpoint — liveness/readiness probe used by Docker and monitoring systems.

Returns a structured ``HealthResponse``. If the FAISS retriever singleton has
not been initialised (or any other dependency raises), the endpoint degrades
gracefully to ``status="degraded"`` with ``faiss_index_loaded=False`` and a
zeroed document count so that orchestrators can still parse the response.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.config import get_settings
from app.core.retriever import get_retriever
from app.models.schemas import HealthResponse
from app.utils.logger import get_logger

router: APIRouter = APIRouter()
_logger = get_logger(__name__)
_VERSION: str = "1.0.0"


@router.get("/health", response_model=HealthResponse, tags=["health"])
async def health_check() -> HealthResponse:
    """Return system status and index statistics.

    Used by Docker ``HEALTHCHECK`` and external monitoring. On any failure to
    reach the retriever singleton the response degrades to ``status="degraded"``
    rather than raising a 5xx, so the probe surface stays predictable.
    """
    settings = get_settings()

    try:
        stats: dict[str, Any] = get_retriever().get_index_stats()
        total_vectors: int = int(stats.get("total_vectors", 0))
        return HealthResponse(
            status="healthy",
            faiss_index_loaded=True,
            total_documents_indexed=total_vectors,
            embedding_model=settings.EMBEDDING_MODEL,
            llm_model=settings.OPENAI_MODEL,
            version=_VERSION,
        )
    except (RuntimeError, FileNotFoundError, ValueError) as exc:
        _logger.error(
            "Health check degraded: error_type=%s message=%s",
            type(exc).__name__,
            str(exc),
        )
        return HealthResponse(
            status="degraded",
            faiss_index_loaded=False,
            total_documents_indexed=0,
            embedding_model=settings.EMBEDDING_MODEL,
            llm_model=settings.OPENAI_MODEL,
            version=_VERSION,
        )
    except Exception as exc:
        _logger.exception(
            "Health check degraded (unexpected): error_type=%s",
            type(exc).__name__,
        )
        return HealthResponse(
            status="degraded",
            faiss_index_loaded=False,
            total_documents_indexed=0,
            embedding_model=settings.EMBEDDING_MODEL,
            llm_model=settings.OPENAI_MODEL,
            version=_VERSION,
        )
