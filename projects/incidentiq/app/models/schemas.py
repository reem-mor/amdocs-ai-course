"""Pydantic V2 schemas for API contracts: query requests, RAG responses, and incident records."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

_ALLOWED_SEVERITIES: frozenset[str] = frozenset({"P1", "P2", "P3"})
_ALLOWED_CONFIDENCE: frozenset[str] = frozenset({"high", "medium", "low", "none"})
_ALLOWED_HEALTH_STATUS: frozenset[str] = frozenset({"healthy", "degraded"})


class QueryRequest(BaseModel):
    """Incoming query from the user."""

    model_config = ConfigDict(str_strip_whitespace=False, extra="forbid")

    question: str = Field(
        ...,
        min_length=3,
        max_length=500,
        description="The incident or SOP question to answer",
    )
    severity_filter: str | None = Field(
        default=None,
        description="Optional severity filter: P1, P2, or P3",
    )

    @field_validator("question")
    @classmethod
    def question_must_not_be_blank(cls, v: str) -> str:
        """Reject queries that are blank or whitespace-only and trim surrounding whitespace."""
        if not v.strip():
            raise ValueError("Question cannot be blank or whitespace only")
        return v.strip()

    @field_validator("severity_filter")
    @classmethod
    def severity_filter_must_be_valid(cls, v: str | None) -> str | None:
        """Normalise severity to uppercase and restrict to the supported levels."""
        if v is None:
            return None
        normalised = v.strip().upper()
        if normalised not in _ALLOWED_SEVERITIES:
            raise ValueError(
                f"severity_filter must be one of {sorted(_ALLOWED_SEVERITIES)}, got {v!r}"
            )
        return normalised


class SourceDocument(BaseModel):
    """A retrieved source document returned with the answer."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="Stable document identifier (e.g. 'INC-001' or 'SOP-007').")
    title: str = Field(..., description="Human-readable document title.")
    severity: str = Field(..., description="Severity label (P1/P2/P3) or 'N/A' for SOPs.")
    category: str = Field(..., description="Operational category, e.g. 'Database', 'Network'.")
    document_type: str = Field(
        ..., description="Document type: 'incident' or 'sop'."
    )
    relevance_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Relevance score in [0.0, 1.0]; higher is more relevant.",
    )
    rank: int = Field(
        ..., ge=1, description="1-based rank within the retrieved result set."
    )


class RAGResponse(BaseModel):
    """Full response returned by the RAG pipeline."""

    model_config = ConfigDict(extra="forbid")

    answer: str = Field(..., description="LLM-generated, context-grounded answer.")
    sources: list[SourceDocument] = Field(
        default_factory=list, description="Source documents used to ground the answer."
    )
    retrieved_count: int = Field(
        ..., ge=0, description="Number of chunks retrieved from the knowledge base."
    )
    confidence: str = Field(
        ..., description="One of 'high', 'medium', 'low', 'none'."
    )
    query: str = Field(..., description="The original (validated) user question.")
    processing_time_ms: int = Field(
        ..., ge=0, description="Total pipeline latency in milliseconds."
    )
    model_used: str = Field(..., description="LLM model identifier used to generate the answer.")

    @field_validator("confidence")
    @classmethod
    def confidence_must_be_valid(cls, v: str) -> str:
        """Restrict confidence to the project-defined bands."""
        if v not in _ALLOWED_CONFIDENCE:
            raise ValueError(
                f"confidence must be one of {sorted(_ALLOWED_CONFIDENCE)}, got {v!r}"
            )
        return v


class HealthResponse(BaseModel):
    """Response from the health check endpoint."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(..., description="Overall service status: 'healthy' or 'degraded'.")
    faiss_index_loaded: bool = Field(
        ..., description="True when the FAISS retriever singleton is ready."
    )
    total_documents_indexed: int = Field(
        ..., ge=0, description="Total vectors currently in the FAISS index."
    )
    embedding_model: str = Field(..., description="Sentence-transformer model identifier.")
    llm_model: str = Field(..., description="OpenAI chat model identifier.")
    version: str = Field(..., description="Application semantic version string.")

    @field_validator("status")
    @classmethod
    def status_must_be_valid(cls, v: str) -> str:
        """Restrict status to 'healthy' or 'degraded'."""
        if v not in _ALLOWED_HEALTH_STATUS:
            raise ValueError(
                f"status must be one of {sorted(_ALLOWED_HEALTH_STATUS)}, got {v!r}"
            )
        return v


class ErrorResponse(BaseModel):
    """Structured error response."""

    model_config = ConfigDict(extra="forbid")

    error: str = Field(..., description="Short error code or category.")
    detail: str = Field(..., description="Human-readable error detail.")
    status_code: int = Field(
        ..., ge=400, le=599, description="HTTP status code associated with the error."
    )
