"""POST /api/query endpoint — orchestrates the RAG pipeline and returns grounded answers.

Wraps the singleton ``RAGPipeline`` with request/response logging, an
``X-Processing-Time`` response header, and a strict error envelope that never
leaks raw exception messages to clients.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.rag_pipeline import get_pipeline
from app.models.schemas import ErrorResponse, QueryRequest, RAGResponse
from app.utils.logger import get_logger

router: APIRouter = APIRouter()
_logger = get_logger(__name__)
_QUERY_LOG_LIMIT: int = 50
_PROCESSING_TIME_HEADER: str = "X-Processing-Time"


@router.post(
    "/query",
    response_model=RAGResponse,
    response_model_exclude_none=False,
    responses={
        422: {"model": ErrorResponse, "description": "Validation error"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
        503: {"model": ErrorResponse, "description": "Pipeline not ready"},
    },
    tags=["rag"],
)
async def query_endpoint(
    request: QueryRequest,
    http_request: Request,
) -> JSONResponse:
    """Run a RAG query: retrieve grounded context and return an LLM-generated answer.

    Args:
        request: Validated ``QueryRequest`` containing the question and an
            optional severity filter.
        http_request: Underlying Starlette request, retained for access to the
            client address and headers when logging.

    Returns:
        On success: a ``JSONResponse`` whose body conforms to ``RAGResponse``
        (grounded answer, ranked sources, confidence band, latency) with an
        ``X-Processing-Time`` header.

        On failure: a ``JSONResponse`` containing an ``ErrorResponse`` body with
        a status code of 422 (validation), 503 (pipeline not initialised), or
        500 (unexpected). Raw exception messages are never returned to the
        client.
    """
    start: float = time.perf_counter()
    client_host: str = http_request.client.host if http_request.client else "-"
    preview: str = _truncate(request.question, _QUERY_LOG_LIMIT)
    _logger.info(
        "Query received: question=%r severity_filter=%s client=%s",
        preview,
        request.severity_filter,
        client_host,
    )

    try:
        response: RAGResponse = await get_pipeline().query(request)
    except ValueError as exc:
        elapsed_ms: int = _elapsed_ms(start)
        _logger.warning(
            "Query validation error: question=%r elapsed_ms=%d error=%s",
            preview,
            elapsed_ms,
            str(exc),
        )
        return _error_response(
            status_code=422,
            error="validation_error",
            detail="The supplied query failed validation.",
            elapsed_ms=elapsed_ms,
        )
    except RuntimeError as exc:
        elapsed_ms = _elapsed_ms(start)
        _logger.error(
            "Query pipeline not ready: question=%r elapsed_ms=%d error=%s",
            preview,
            elapsed_ms,
            str(exc),
        )
        return _error_response(
            status_code=503,
            error="service_unavailable",
            detail="RAG pipeline is not ready. Please retry shortly.",
            elapsed_ms=elapsed_ms,
        )
    except Exception:
        elapsed_ms = _elapsed_ms(start)
        _logger.exception(
            "Query failed unexpectedly: question=%r elapsed_ms=%d",
            preview,
            elapsed_ms,
        )
        return _error_response(
            status_code=500,
            error="internal_error",
            detail="An internal error occurred while processing the query.",
            elapsed_ms=elapsed_ms,
        )

    elapsed_ms = _elapsed_ms(start)
    _logger.info(
        "Query complete: confidence=%s sources=%d time=%dms",
        response.confidence,
        len(response.sources),
        elapsed_ms,
    )

    json_payload: JSONResponse = JSONResponse(
        content=response.model_dump(),
        status_code=200,
        headers={_PROCESSING_TIME_HEADER: f"{elapsed_ms}ms"},
    )
    return json_payload


def _error_response(
    status_code: int,
    error: str,
    detail: str,
    elapsed_ms: int,
) -> JSONResponse:
    """Build a JSONResponse wrapping an ``ErrorResponse`` with the processing-time header."""
    body = ErrorResponse(error=error, detail=detail, status_code=status_code)
    return JSONResponse(
        content=body.model_dump(),
        status_code=status_code,
        headers={_PROCESSING_TIME_HEADER: f"{elapsed_ms}ms"},
    )


def _elapsed_ms(start: float) -> int:
    """Return integer milliseconds elapsed since the monotonic timestamp ``start``."""
    return int((time.perf_counter() - start) * 1000)


def _truncate(text: str, limit: int) -> str:
    """Return ``text`` shortened to ``limit`` characters with an ellipsis suffix when truncated."""
    if len(text) <= limit:
        return text
    return text[:limit] + "..."
