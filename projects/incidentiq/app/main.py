"""FastAPI application entrypoint for IncidentIQ.

Wires up the lifespan manager (FAISS retriever + RAG pipeline initialisation),
CORS middleware, static file mount, routers, root route, and a global exception
handler that never leaks internal error details.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import health, query
from app.config import get_settings
from app.core.rag_pipeline import init_pipeline
from app.core.retriever import init_retriever
from app.models.schemas import ErrorResponse
from app.utils.logger import get_logger

_PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
_STATIC_DIR: Path = _PROJECT_ROOT / "frontend" / "static"
_INDEX_HTML: Path = _STATIC_DIR / "index.html"

logger = get_logger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Initialise heavy dependencies on startup and log graceful shutdown.

    Startup:
        1. Load the FAISS retriever singleton from ``settings.FAISS_INDEX_PATH``.
        2. Construct the RAG pipeline singleton (retriever + LLM client).
        3. Emit a readiness log line with the bound port.

    Shutdown:
        Emit a single shutdown log line. Singletons are garbage-collected by
        the interpreter; no explicit teardown is required for FAISS or the
        async OpenAI client.
    """
    logger.info("IncidentIQ starting up...")

    index_path: Path = settings.faiss_index_path
    init_retriever(index_path)
    logger.info("FAISS index loaded successfully: path=%s", index_path)

    init_pipeline()
    logger.info("RAG pipeline initialized")

    logger.info(
        "IncidentIQ ready \u2014 listening on port %d", settings.APP_PORT
    )

    try:
        yield
    finally:
        logger.info("IncidentIQ shutting down...")


app: FastAPI = FastAPI(
    title="IncidentIQ",
    description=(
        "Enterprise Incident Intelligence Platform \u2014 RAG-powered "
        "incident management, SOP guidance, and MTTR reduction"
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
    logger.info("Static files mounted: path=%s", _STATIC_DIR)
else:
    logger.warning(
        "Static directory not found, skipping /static mount: path=%s",
        _STATIC_DIR,
    )

app.include_router(health.router, prefix="", tags=["health"])
app.include_router(query.router, prefix="/api", tags=["rag"])


@app.get("/", include_in_schema=False, response_model=None)
async def root() -> FileResponse | JSONResponse:
    """Serve the SPA entrypoint when available, otherwise expose a discovery payload.

    Returns:
        A ``FileResponse`` for ``frontend/static/index.html`` when present and
        non-empty, otherwise a small JSON document pointing clients at the
        docs and health endpoints.
    """
    if _INDEX_HTML.is_file() and _INDEX_HTML.stat().st_size > 0:
        return FileResponse(path=_INDEX_HTML, media_type="text/html")
    return JSONResponse(
        content={
            "message": "IncidentIQ API",
            "docs": "/docs",
            "health": "/health",
        },
        status_code=200,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Catch-all handler that logs the full traceback and returns a sanitised 500.

    This is the last line of defence against unhandled exceptions in routes or
    middleware. Raw exception messages are deliberately not surfaced to clients
    to avoid leaking internal details.
    """
    logger.exception(
        "Unhandled exception: path=%s method=%s error_type=%s",
        request.url.path,
        request.method,
        type(exc).__name__,
    )
    body = ErrorResponse(
        error="internal_error",
        detail="An unexpected internal error occurred.",
        status_code=500,
    )
    return JSONResponse(content=body.model_dump(), status_code=500)
