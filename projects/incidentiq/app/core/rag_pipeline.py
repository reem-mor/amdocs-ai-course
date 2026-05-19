"""End-to-end RAG pipeline: embed query, retrieve context, generate grounded answer.

The pipeline orchestrates the singleton retriever and LLM client into a single
async `query()` entrypoint that takes a validated `QueryRequest` and returns a
`RAGResponse` with grounded answer text, ranked sources, confidence band, and
latency metrics.
"""

from __future__ import annotations

import time
from threading import Lock
from typing import Any

from app.config import get_settings
from app.core.llm_client import get_llm_client
from app.core.retriever import get_retriever
from app.models.schemas import QueryRequest, RAGResponse, SourceDocument
from app.utils.logger import get_logger

SYSTEM_PROMPT: str = """You are IncidentIQ, an expert enterprise incident response assistant. You help on-call engineers and NOC teams resolve incidents faster and reduce MTTR.

Your rules:
- Answer ONLY based on the retrieved context provided
- If context does not contain enough information, say clearly:
  "I don't have enough information in the knowledge base to answer this question. Please escalate to your team lead."
- Be precise, technical, and actionable
- Always structure your response with these exact sections:

## Assessment
[What is happening and the likely impact]

## Triage Steps
[Numbered diagnostic steps to identify root cause]

## Resolution Steps  
[Numbered steps to resolve the issue]

## Escalation
[When and who to escalate to if steps don't resolve]

- Use exact commands where available from the context
- Never hallucinate commands or procedures not in the context
- Keep responses concise but complete"""

_NO_RESULTS_ANSWER: str = (
    "No relevant incidents or SOPs found for your query. "
    "Please rephrase or escalate to your team lead."
)
_QUERY_LOG_LIMIT: int = 50


class RAGPipeline:
    """Main RAG orchestration pipeline for IncidentIQ."""

    def __init__(self) -> None:
        """Wire up the retriever, LLM client, settings, and logger singletons.

        Raises:
            RuntimeError: If the FAISS retriever singleton has not been
                initialised via `init_retriever()` prior to this call.
        """
        self._retriever = get_retriever()
        self._llm_client = get_llm_client()
        self._settings = get_settings()
        self._logger = get_logger(__name__)
        self._logger.info("RAG Pipeline initialized")

    async def query(self, request: QueryRequest) -> RAGResponse:
        """Run the full retrieve → augment → generate flow for a user question.

        Args:
            request: Validated `QueryRequest` containing the question and an
                optional severity filter.

        Returns:
            A `RAGResponse` with the grounded answer, ranked source documents,
            confidence band, retrieved count, latency, and model identifier.
        """
        start: float = time.perf_counter()
        query_preview: str = _truncate(request.question, _QUERY_LOG_LIMIT)
        self._logger.info(
            "RAG query received: query=%r severity_filter=%s top_k=%d",
            query_preview,
            request.severity_filter,
            self._settings.TOP_K_RESULTS,
        )

        raw_results: list[dict[str, Any]] = self._retriever.retrieve(
            query=request.question,
            top_k=self._settings.TOP_K_RESULTS,
        )

        if request.severity_filter is not None:
            target: str = request.severity_filter
            raw_results = [
                r for r in raw_results
                if str(r.get("metadata", {}).get("severity", "")).upper() == target
            ]
            for new_rank, item in enumerate(raw_results, start=1):
                item["rank"] = new_rank
            self._logger.info(
                "Severity filter applied: severity=%s remaining_results=%d",
                target,
                len(raw_results),
            )

        if not raw_results:
            elapsed_ms: int = _elapsed_ms(start)
            self._logger.info(
                "RAG completed (no results): query=%r confidence=none elapsed_ms=%d",
                query_preview,
                elapsed_ms,
            )
            return RAGResponse(
                answer=_NO_RESULTS_ANSWER,
                sources=[],
                retrieved_count=0,
                confidence="none",
                query=request.question,
                processing_time_ms=elapsed_ms,
                model_used=self._settings.OPENAI_MODEL,
            )

        context_string: str = _build_context(raw_results)
        user_prompt: str = (
            "Context from knowledge base:\n"
            f"{context_string}\n\n"
            f"Question: {request.question}\n\n"
            "Provide a structured response based only on the above context."
        )

        answer: str = await self._llm_client.generate(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )

        top_score: float = float(raw_results[0].get("score", 0.0))
        confidence: str = _confidence_band(top_score)

        sources: list[SourceDocument] = [
            _to_source_document(r) for r in raw_results
        ]

        elapsed_ms = _elapsed_ms(start)
        self._logger.info(
            "RAG completed: query=%r confidence=%s retrieved_count=%d top_score=%.3f elapsed_ms=%d",
            query_preview,
            confidence,
            len(sources),
            top_score,
            elapsed_ms,
        )

        return RAGResponse(
            answer=answer,
            sources=sources,
            retrieved_count=len(sources),
            confidence=confidence,
            query=request.question,
            processing_time_ms=elapsed_ms,
            model_used=self._settings.OPENAI_MODEL,
        )


def _build_context(results: list[dict[str, Any]]) -> str:
    """Render retrieved chunks into the prompt-ready context block."""
    blocks: list[str] = []
    for item in results:
        meta: dict[str, Any] = item.get("metadata", {})
        rank: int = int(item.get("rank", 0))
        title: str = str(meta.get("title", "Unknown"))
        severity: str = str(meta.get("severity", "N/A"))
        category: str = str(meta.get("category", "Unknown"))
        chunk_text: str = str(item.get("chunk_text", ""))
        blocks.append(
            f"--- Source {rank}: {title} [{severity} - {category}] ---\n"
            f"{chunk_text}\n"
            f"---"
        )
    return "\n".join(blocks)


def _to_source_document(result: dict[str, Any]) -> SourceDocument:
    """Convert a raw retriever result dict into a typed `SourceDocument`."""
    meta: dict[str, Any] = result.get("metadata", {})
    raw_score: float = float(result.get("score", 0.0))
    clamped_score: float = max(0.0, min(1.0, raw_score))
    return SourceDocument(
        id=str(meta.get("id", "")),
        title=str(meta.get("title", "")),
        severity=str(meta.get("severity", "N/A")),
        category=str(meta.get("category", "Unknown")),
        document_type=str(meta.get("document_type", "unknown")),
        relevance_score=clamped_score,
        rank=int(result.get("rank", 1)),
    )


def _confidence_band(top_score: float) -> str:
    """Map a top retrieval score to a confidence band ('high'/'medium'/'low'/'none')."""
    if top_score >= 0.7:
        return "high"
    if top_score >= 0.4:
        return "medium"
    if top_score > 0.0:
        return "low"
    return "none"


def _elapsed_ms(start: float) -> int:
    """Return integer milliseconds elapsed since the monotonic timestamp `start`."""
    return int((time.perf_counter() - start) * 1000)


def _truncate(text: str, limit: int) -> str:
    """Return `text` shortened to `limit` characters with an ellipsis suffix when truncated."""
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


_pipeline_instance: RAGPipeline | None = None
_pipeline_lock: Lock = Lock()


def get_pipeline() -> RAGPipeline:
    """Return the singleton pipeline instance.

    Raises:
        RuntimeError: If `init_pipeline()` has not yet been called.
    """
    global _pipeline_instance
    if _pipeline_instance is None:
        raise RuntimeError(
            "Pipeline not initialized. Call init_pipeline() first."
        )
    return _pipeline_instance


def init_pipeline() -> RAGPipeline:
    """Initialize and return the singleton RAG pipeline (idempotent, thread-safe)."""
    global _pipeline_instance
    with _pipeline_lock:
        _pipeline_instance = RAGPipeline()
    return _pipeline_instance
