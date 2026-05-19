"""Centralized application configuration loaded from environment variables via pydantic-settings.

Exposes a single `Settings` model and a cached `get_settings()` accessor so that
configuration is parsed exactly once per process and reused across all callers.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
_ENV_FILE: Path = _PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    """Strongly-typed application settings sourced from environment variables and `.env`.

    All fields are validated by Pydantic V2 at instantiation time. Secrets (e.g.
    `OPENAI_API_KEY`) are never logged. The `.env` file at the project root is
    loaded automatically; explicit environment variables take precedence over
    file values.
    """

    OPENAI_API_KEY: str = Field(
        ...,
        description="OpenAI API key used for chat completions. Required.",
    )
    OPENAI_MODEL: str = Field(
        default="gpt-4o-mini",
        description="OpenAI chat model identifier used by the LLM client.",
    )
    EMBEDDING_MODEL: str = Field(
        default="all-MiniLM-L6-v2",
        description="Sentence-transformer model used to embed text into dense vectors.",
    )
    FAISS_INDEX_PATH: str = Field(
        default="knowledge_base/faiss_index",
        description="Filesystem path (relative to project root) for the FAISS index.",
    )
    TOP_K_RESULTS: int = Field(
        default=5,
        ge=1,
        description="Number of top-K nearest neighbors retrieved per query.",
    )
    MAX_TOKENS: int = Field(
        default=1000,
        ge=1,
        description="Maximum number of tokens generated per LLM completion.",
    )
    APP_PORT: int = Field(
        default=8000,
        ge=1,
        le=65535,
        description="TCP port on which the FastAPI server listens.",
    )

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @property
    def faiss_index_path(self) -> Path:
        """Return the absolute `Path` to the FAISS index directory."""
        raw = Path(self.FAISS_INDEX_PATH)
        return raw if raw.is_absolute() else _PROJECT_ROOT / raw


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a process-wide cached `Settings` instance.

    The first call parses the environment and `.env`; subsequent calls reuse the
    same object. Tests can clear the cache via `get_settings.cache_clear()`.
    """
    return Settings()
