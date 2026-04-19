"""Resilient LLM factory with automatic fallback.

Reads PRIMARY_LLM_PROVIDER / FALLBACK_LLM_PROVIDER from config and
provides a wrapper that transparently retries with the fallback when
the primary throws a rate-limit, auth, or connectivity error.

Usage (outside agent/ — we do NOT modify agent files):
    from utils.llm_router import get_llm_with_fallback
    llm = get_llm_with_fallback()
    response = llm.invoke(prompt)   # auto-fallback on failure
"""

from __future__ import annotations

from utils import config
from utils.logger import get_logger

log = get_logger(__name__)

# Exception types that trigger a fallback retry
_FALLBACK_ERRORS = (
    ConnectionError,
    TimeoutError,
    OSError,
)

# String fragments in error messages that indicate rate-limit / auth issues
_RETRYABLE_MESSAGES = (
    "rate limit",
    "rate_limit",
    "ratelimit",
    "429",
    "quota",
    "authentication",
    "unauthorized",
    "401",
    "403",
    "permission",
    "access denied",
    "throttl",
)


def _is_retryable(exc: Exception) -> bool:
    """Return True if the exception should trigger a fallback."""
    if isinstance(exc, _FALLBACK_ERRORS):
        return True
    msg = str(exc).lower()
    return any(fragment in msg for fragment in _RETRYABLE_MESSAGES)


def get_llm(provider: str, model: str):
    """Instantiate a LangChain chat model for the given provider/model.

    Supports: mock, bedrock, anthropic, google, ollama, openai (default).
    """
    if provider == "mock":
        from utils.mock_llm import MockLLM
        return MockLLM()
    if provider == "bedrock":
        from langchain_aws import ChatBedrockConverse
        return ChatBedrockConverse(model=model, temperature=0)
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=model, temperature=0)
    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=model, temperature=0)
    if provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(model=model, temperature=0)
    # Default: OpenAI-compatible
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(model=model, temperature=0)


class ResilientLLM:
    """Wrapper that tries the primary LLM and falls back transparently.

    Exposes the same `.invoke()` interface as LangChain chat models so
    it can be used as a drop-in replacement.
    """

    def __init__(self):
        self._primary_provider = config.PRIMARY_LLM_PROVIDER
        self._primary_model = config.PRIMARY_LLM_MODEL
        self._fallback_provider = config.FALLBACK_LLM_PROVIDER
        self._fallback_model = config.FALLBACK_LLM_MODEL

    def invoke(self, prompt, **kwargs):
        """Invoke the primary LLM; on retryable error, switch to fallback."""
        # ── Primary attempt ──────────────────────────────────────
        try:
            primary = get_llm(self._primary_provider, self._primary_model)
            return primary.invoke(prompt, **kwargs)
        except Exception as exc:
            if not _is_retryable(exc):
                raise  # Non-retryable errors propagate immediately

            log.warning(
                "llm_primary_failed",
                provider=self._primary_provider,
                error=str(exc),
                action="falling_back",
            )

        # ── Fallback attempt ─────────────────────────────────────
        if not self._fallback_provider:
            raise RuntimeError(
                f"Primary LLM ({self._primary_provider}) failed and no "
                f"FALLBACK_LLM_PROVIDER is configured."
            )

        try:
            fallback = get_llm(self._fallback_provider, self._fallback_model)
            response = fallback.invoke(prompt, **kwargs)
            log.info(
                "llm_fallback_success",
                provider=self._fallback_provider,
            )
            return response
        except Exception as fallback_exc:
            log.error(
                "llm_fallback_also_failed",
                provider=self._fallback_provider,
                error=str(fallback_exc),
            )
            raise


def get_llm_with_fallback() -> ResilientLLM:
    """Return a ResilientLLM instance configured from .env."""
    return ResilientLLM()
