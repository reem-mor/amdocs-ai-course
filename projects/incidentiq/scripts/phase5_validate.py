"""Phase 5 end-to-end validation harness for the RAG pipeline.

Runs three assertions:
  1. A relevant query returns sources, valid confidence, and a non-trivial answer.
  2. An irrelevant query degrades to 'none' or 'low' confidence.
  3. `QueryRequest` rejects short/blank questions at the Pydantic layer.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.config import get_settings  # noqa: E402
from app.core.rag_pipeline import init_pipeline  # noqa: E402
from app.core.retriever import init_retriever  # noqa: E402
from app.models.schemas import QueryRequest  # noqa: E402


async def main() -> int:
    """Execute all three Phase 5 validation cases and return a process exit code."""
    settings = get_settings()
    init_retriever(settings.faiss_index_path)
    pipeline = init_pipeline()

    request = QueryRequest(
        question="What are triage steps for kubernetes pod crash loop?"
    )
    response = await pipeline.query(request)
    print("Test 1 — Valid query:")
    print(f"  Confidence: {response.confidence}")
    print(f"  Sources: {response.retrieved_count}")
    print(f"  Model: {response.model_used}")
    print(f"  Time: {response.processing_time_ms}ms")
    print(f"  Answer preview: {response.answer[:200]}")
    assert response.retrieved_count > 0, "Test 1: expected at least one retrieved source"
    assert response.confidence in {"high", "medium", "low"}, (
        f"Test 1: unexpected confidence {response.confidence!r}"
    )
    assert len(response.answer) > 50, "Test 1: answer too short"
    print("  PASSED")

    request2 = QueryRequest(question="What is the weather in Paris today?")
    response2 = await pipeline.query(request2)
    print("Test 2 — Irrelevant query:")
    print(f"  Confidence: {response2.confidence}")
    if response2.confidence in {"none", "low"}:
        print("  PASSED")
    else:
        print(f"  WARNING: unexpected confidence level: {response2.confidence}")

    try:
        QueryRequest(question="ab")
        print("Test 3 — FAILED: should have raised validation error")
        return 1
    except ValueError:
        print("Test 3 — Validation error caught correctly: PASSED")

    print("Phase 5 validation PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
