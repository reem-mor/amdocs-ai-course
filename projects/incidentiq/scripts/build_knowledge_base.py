"""Offline pipeline: ingest raw incident data, embed it, and persist the FAISS index to disk.

Run with: `python scripts/build_knowledge_base.py`

Reads incidents + SOPs from `data.sample_incidents`, formats each record into a
dense readable paragraph, splits at sentence boundaries if a chunk exceeds the
configured token budget, embeds the chunks via the shared sentence-transformer
model, builds an `IndexFlatL2` FAISS index, and writes `index.faiss` +
`metadata.json` to the configured output directory.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import faiss
import numpy as np

_PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.config import get_settings  # noqa: E402
from app.core.embedder import get_embedding_model  # noqa: E402
from app.utils.logger import get_logger  # noqa: E402
from data.sample_incidents import get_all_documents  # noqa: E402

_logger = get_logger(__name__)

_TOKENS_PER_WORD: float = 1.0 / 0.75
_SENTENCE_SPLIT_RE: re.Pattern[str] = re.compile(r"(?<=[.!?])\s+")


def _format_numbered_steps(steps: list[str]) -> str:
    """Render a list of step strings as `1) step ... 2) step ...`."""
    return " ".join(f"{i}) {step}" for i, step in enumerate(steps, start=1))


def _format_tags(tags: list[str]) -> str:
    """Render the tag list as a comma-separated string (empty if no tags)."""
    return ", ".join(tags) if tags else "none"


def format_incident_chunk(incident: dict[str, Any]) -> str:
    """Format an incident dict into a single rich, readable paragraph.

    The resulting string is dense enough that the retriever surfaces concrete
    triage and resolution guidance, while still being LLM-friendly prose.
    """
    incident_id: str = incident["id"]
    severity: str = incident.get("severity", "N/A")
    category: str = incident.get("category", "Unknown")
    title: str = incident["title"]
    description: str = incident.get("description", "").strip()
    triage_steps: list[str] = incident.get("triage_steps", [])
    root_cause: str = incident.get("root_cause", "").strip()
    resolution_steps: list[str] = incident.get("resolution_steps", [])
    mttr_minutes: Any = incident.get("mttr_minutes", "N/A")
    lessons_learned: str = incident.get("lessons_learned", "").strip()
    tags: list[str] = incident.get("tags", [])

    parts: list[str] = [
        f"Incident {incident_id} [{severity} - {category}]: {title}.",
        f"Description: {description}." if description else "",
        (
            f"Triage Steps: {_format_numbered_steps(triage_steps)}."
            if triage_steps
            else ""
        ),
        f"Root Cause: {root_cause}." if root_cause else "",
        (
            f"Resolution: {_format_numbered_steps(resolution_steps)}."
            if resolution_steps
            else ""
        ),
        f"MTTR: {mttr_minutes} minutes." if mttr_minutes != "N/A" else "",
        f"Lessons Learned: {lessons_learned}." if lessons_learned else "",
        f"Tags: {_format_tags(tags)}.",
    ]
    return " ".join(part for part in parts if part)


def format_sop_chunk(sop: dict[str, Any]) -> str:
    """Format an SOP dict into a single rich, readable paragraph."""
    sop_id: str = sop["id"]
    title: str = sop["title"]
    version: str = sop.get("version", "1.0")
    applicability: str = sop.get("applicability", "").strip()
    severity_trigger: str = sop.get("severity_trigger", "N/A")
    prerequisites: list[str] = sop.get("prerequisites", [])
    steps: list[str] = sop.get("steps", [])
    escalation_path: str = sop.get("escalation_path", "").strip()
    owner: str = sop.get("owner", "Unassigned")

    prerequisites_str: str = (
        "; ".join(prerequisites) if prerequisites else "none documented"
    )

    parts: list[str] = [
        f"SOP {sop_id} [{title}] Version {version}.",
        f"Applies to: {applicability}." if applicability else "",
        f"Severity Trigger: {severity_trigger}.",
        f"Prerequisites: {prerequisites_str}.",
        f"Steps: {_format_numbered_steps(steps)}." if steps else "",
        f"Escalation: {escalation_path}." if escalation_path else "",
        f"Owner: {owner}.",
    ]
    return " ".join(part for part in parts if part)


def format_reference_chunk(ref: dict[str, Any]) -> str:
    """Format an external reference document into a single rich, readable paragraph.

    References are curated summaries of public SRE/post-mortem material (Google SRE
    Book, AWS Well-Architected, CNCF, Confluent, etc.). They have a different shape
    than incidents/SOPs: no triage/resolution steps, but include a `content` body,
    optional `mttr_impact`, and a list of `key_concepts`.
    """
    ref_id: str = ref["id"]
    title: str = ref["title"]
    source: str = ref.get("source", "Unknown source")
    category: str = ref.get("category", "Reference")
    content: str = ref.get("content", "").strip()
    mttr_impact: str = ref.get("mttr_impact", "").strip()
    key_concepts: list[str] = ref.get("key_concepts", [])
    tags: list[str] = ref.get("tags", [])

    concepts_str: str = ", ".join(key_concepts) if key_concepts else "none"

    parts: list[str] = [
        f"Reference {ref_id} [{category}]: {title}.",
        f"Source: {source}.",
        content if content else "",
        f"MTTR Impact: {mttr_impact}." if mttr_impact else "",
        f"Key Concepts: {concepts_str}.",
        f"Tags: {_format_tags(tags)}.",
    ]
    return " ".join(part for part in parts if part)


def _classify_document(doc: dict[str, Any]) -> str:
    """Return the document type tag for `doc`: 'incident', 'reference', or 'sop'.

    Detection order matters: an explicit `document_type` field wins; otherwise we
    fall back to legacy shape detection — anything with a `severity` field is an
    incident, anything else is an SOP. This keeps the original 60-document corpus
    working unchanged while admitting the new reference shape.
    """
    explicit: str | None = doc.get("document_type")
    if explicit in {"incident", "reference", "sop"}:
        return explicit
    if "severity" in doc:
        return "incident"
    return "sop"


def _split_sentences(text: str) -> list[str]:
    """Split `text` into sentences using a simple `[.!?]` boundary heuristic."""
    pieces: list[str] = _SENTENCE_SPLIT_RE.split(text.strip())
    return [piece for piece in pieces if piece]


def chunk_text(text: str, max_tokens: int = 512) -> list[str]:
    """Split `text` into chunks bounded by `max_tokens`.

    Splits at sentence boundaries only. If a single sentence already exceeds
    the budget it is emitted as its own chunk rather than being broken
    mid-sentence (the embedder will simply truncate it).

    Args:
        text: Source string. Returns `[]` if empty after stripping whitespace.
        max_tokens: Approximate per-chunk token budget. The conversion uses
            1 token ≈ 0.75 words, so `max_words = int(max_tokens * 0.75)`.

    Returns:
        List of chunk strings, each at or below the budget when possible.
    """
    if max_tokens <= 0:
        raise ValueError(f"max_tokens must be positive, got {max_tokens}")

    cleaned: str = text.strip()
    if not cleaned:
        return []

    max_words: int = max(1, int(max_tokens * 0.75))
    total_words: int = len(cleaned.split())
    if total_words <= max_words:
        return [cleaned]

    sentences: list[str] = _split_sentences(cleaned)
    chunks: list[str] = []
    current: list[str] = []
    current_words: int = 0

    for sentence in sentences:
        word_count: int = len(sentence.split())
        if current and current_words + word_count > max_words:
            chunks.append(" ".join(current))
            current = [sentence]
            current_words = word_count
        else:
            current.append(sentence)
            current_words += word_count

    if current:
        chunks.append(" ".join(current))

    return chunks


def _estimate_tokens(text: str) -> int:
    """Estimate the token count of `text` using the 1 token ≈ 0.75 words rule."""
    return int(len(text.split()) * _TOKENS_PER_WORD)


def build_index(documents: list[dict[str, Any]], output_path: Path) -> None:
    """Format, chunk, embed, and persist a FAISS index for `documents`.

    Args:
        documents: Combined list of incident and SOP dicts. Incidents are
            detected by the presence of a `severity` field.
        output_path: Directory that will hold `index.faiss` and
            `metadata.json`. The directory must already exist.

    Raises:
        ValueError: If `documents` is empty or no usable chunks are produced.
    """
    if not documents:
        raise ValueError("No documents supplied — refusing to build an empty index.")

    start: float = time.perf_counter()
    output_path = Path(output_path)
    output_path.mkdir(parents=True, exist_ok=True)

    metadata: list[dict[str, Any]] = []
    chunks: list[str] = []

    for doc in documents:
        doc_type: str = _classify_document(doc)
        if doc_type == "incident":
            formatted: str = format_incident_chunk(doc)
        elif doc_type == "reference":
            formatted = format_reference_chunk(doc)
        else:
            formatted = format_sop_chunk(doc)

        doc_chunks: list[str] = chunk_text(formatted, max_tokens=512)
        if not doc_chunks:
            _logger.warning("Skipping document %s — produced 0 chunks", doc.get("id"))
            continue

        default_category: str = "SOP" if doc_type == "sop" else "Reference"
        for chunk_index, chunk in enumerate(doc_chunks):
            chunks.append(chunk)
            metadata.append(
                {
                    "id": doc["id"],
                    "title": doc.get("title", ""),
                    "severity": doc.get("severity", "N/A"),
                    "category": doc.get("category", default_category),
                    "tags": list(doc.get("tags", [])),
                    "document_type": doc_type,
                    "chunk_index": chunk_index,
                    "chunk_text": chunk,
                }
            )

    if not chunks:
        raise ValueError("No chunks were produced from the provided documents.")

    _logger.info(
        "Embedding %d chunks from %d documents...", len(chunks), len(documents)
    )
    embedder = get_embedding_model()
    vectors: np.ndarray = embedder.embed(chunks)
    if vectors.dtype != np.float32:
        vectors = vectors.astype(np.float32)
    dimension: int = int(vectors.shape[1])

    index: faiss.IndexFlatL2 = faiss.IndexFlatL2(dimension)
    index.add(vectors)

    index_file: Path = output_path / "index.faiss"
    metadata_file: Path = output_path / "metadata.json"
    faiss.write_index(index, str(index_file))
    with metadata_file.open("w", encoding="utf-8") as fh:
        json.dump(metadata, fh, ensure_ascii=False, indent=2)

    elapsed: float = time.perf_counter() - start
    _logger.info("Knowledge base built successfully")
    _logger.info("Total documents: %d", len(documents))
    _logger.info("Total chunks: %d", len(chunks))
    _logger.info("Embedding dimension: %d", dimension)
    _logger.info("Index size: %d vectors", index.ntotal)
    _logger.info("Time taken: %.1fs", elapsed)
    _logger.info("Output: %s", output_path)


def _main() -> int:
    """Entry point: load documents, build the index, log summary, return exit code."""
    try:
        documents: list[dict[str, Any]] = get_all_documents()
        settings = get_settings()
        output_path: Path = settings.faiss_index_path
        output_path.mkdir(parents=True, exist_ok=True)
        build_index(documents, output_path)
        return 0
    except FileNotFoundError as exc:
        _logger.error("Required file missing during ingestion: %s", exc)
        return 1
    except ValueError as exc:
        _logger.error("Invalid input to ingestion pipeline: %s", exc)
        return 1
    except (OSError, RuntimeError) as exc:
        _logger.exception("Ingestion failed with system error: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(_main())
