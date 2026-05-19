"""Async OpenAI client wrapper — handles chat completions with retries and structured logging.

The client is exposed as a process-wide singleton via `get_llm_client()`. It wraps
`openai.AsyncOpenAI` with exponential-backoff retry logic (1s, 2s, 4s) on transient
failures and emits structured, redacted log lines on every attempt.
"""

from __future__ import annotations

import asyncio
from threading import Lock

from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)

from app.config import get_settings
from app.utils.logger import get_logger

_MAX_ATTEMPTS: int = 3
_BACKOFF_BASE_SECONDS: float = 1.0
_USER_PROMPT_LOG_LIMIT: int = 50

_RETRYABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    InternalServerError,
)


class LLMClient:
    """Async OpenAI LLM client with bounded exponential-backoff retry logic."""

    def __init__(self) -> None:
        """Build an `AsyncOpenAI` client from project settings.

        Raises:
            ValueError: If `OPENAI_API_KEY` is missing or empty (enforced by settings).
        """
        settings = get_settings()
        self.client: AsyncOpenAI = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        self.model: str = settings.OPENAI_MODEL
        self.max_tokens: int = settings.MAX_TOKENS
        self.logger = get_logger(__name__)
        self.logger.info(
            "LLM client initialized: model=%s max_tokens=%d",
            self.model,
            self.max_tokens,
        )

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
    ) -> str:
        """Send a chat completion request to OpenAI and return the response text.

        Retries up to 3 times on transient OpenAI errors (connection, timeout,
        rate-limit, 5xx) with 1s/2s/4s exponential backoff. Non-retryable errors
        (e.g. auth, bad request) fail fast on the first attempt.

        Args:
            system_prompt: System message defining model behaviour.
            user_prompt: User-facing prompt. Only the first 50 chars are logged.
            temperature: Sampling temperature; defaults to 0.1 for grounded responses.

        Returns:
            The assistant message content as a plain string.

        Raises:
            RuntimeError: If all retry attempts are exhausted, or if the API
                returns an empty/malformed response.
        """
        prompt_preview: str = _truncate(user_prompt, _USER_PROMPT_LOG_LIMIT)

        last_error: BaseException | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=temperature,
                    max_tokens=self.max_tokens,
                )
            except _RETRYABLE_EXCEPTIONS as exc:
                last_error = exc
                self.logger.warning(
                    "LLM retryable error: attempt=%d/%d error_type=%s message=%s",
                    attempt,
                    _MAX_ATTEMPTS,
                    type(exc).__name__,
                    str(exc),
                )
                if attempt == _MAX_ATTEMPTS:
                    break
                backoff: float = _BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
                await asyncio.sleep(backoff)
                continue
            except APIError as exc:
                self.logger.error(
                    "LLM non-retryable API error: attempt=%d error_type=%s message=%s",
                    attempt,
                    type(exc).__name__,
                    str(exc),
                )
                raise RuntimeError(
                    f"OpenAI API error ({type(exc).__name__}): {exc}"
                ) from exc

            choices = getattr(response, "choices", None) or []
            if not choices or choices[0].message is None or choices[0].message.content is None:
                self.logger.error(
                    "LLM returned empty response: attempt=%d model=%s prompt_preview=%r",
                    attempt,
                    self.model,
                    prompt_preview,
                )
                raise RuntimeError("OpenAI returned an empty response with no choices.")

            content: str = choices[0].message.content
            usage = getattr(response, "usage", None)
            prompt_tokens: int = int(getattr(usage, "prompt_tokens", 0) or 0)
            completion_tokens: int = int(getattr(usage, "completion_tokens", 0) or 0)

            self.logger.info(
                "LLM success: model=%s attempt=%d prompt_tokens=%d completion_tokens=%d total_tokens=%d prompt_preview=%r",
                self.model,
                attempt,
                prompt_tokens,
                completion_tokens,
                prompt_tokens + completion_tokens,
                prompt_preview,
            )
            return content

        raise RuntimeError(
            f"OpenAI request failed after {_MAX_ATTEMPTS} attempts "
            f"(last error: {type(last_error).__name__ if last_error else 'unknown'}: {last_error})"
        )


def _truncate(text: str, limit: int) -> str:
    """Return `text` shortened to `limit` characters with an ellipsis suffix when truncated."""
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


_llm_client_instance: LLMClient | None = None
_llm_client_lock: Lock = Lock()


def get_llm_client() -> LLMClient:
    """Return the singleton LLM client, constructing it on first access (thread-safe)."""
    global _llm_client_instance
    if _llm_client_instance is None:
        with _llm_client_lock:
            if _llm_client_instance is None:
                _llm_client_instance = LLMClient()
    return _llm_client_instance
