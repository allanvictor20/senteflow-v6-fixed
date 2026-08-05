"""
SenteFlow — Multi-Provider LLM Abstraction (IDEA 06, Groq edition)
===================================================================
Prevents SenteFlow from going offline when the primary LLM provider is
unavailable or rate-limited.

Provider chain: Groq → Claude Haiku → OpenAI gpt-4o-mini
Each provider implements LLMProvider.complete(). On quota/rate errors,
the next provider is tried silently. Owner sees no disruption.

Groq is OpenAI-compatible, so the GroqProvider uses the `openai` SDK
pointed at https://api.groq.com/openai/v1. Default model:
`llama-3.3-70b-versatile`. Override with the GROQ_MODEL env var.

Usage:
    from services.llm.llm_provider import complete_with_fallback
    result = await complete_with_fallback(prompt, system_prompt)
"""

import logging
import os
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)

# Default Groq model. Override with the GROQ_MODEL env var.
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"


class LLMProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def complete(self, prompt: str, system: str, max_tokens: int = 500) -> str:
        """Send prompt + system to the LLM, return raw text response."""

    def _is_quota_error(self, exc: Exception) -> bool:
        msg = str(exc).lower()
        return any(k in msg for k in ("quota", "rate", "limit", "429", "exhausted", "resource_exhausted"))


class GroqProvider(LLMProvider):
    """
    Primary provider. Uses Groq's OpenAI-compatible endpoint via the `openai`
    SDK (already in requirements). Default model: llama-3.3-70b-versatile.
    """

    name = "groq"

    async def complete(self, prompt: str, system: str, max_tokens: int = 500) -> str:
        from openai import AsyncOpenAI

        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY not set")

        model = os.environ.get("GROQ_MODEL", DEFAULT_GROQ_MODEL)
        client = AsyncOpenAI(api_key=api_key, base_url=GROQ_BASE_URL)
        response = await client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            temperature=0.1,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        )
        return (response.choices[0].message.content or "").strip()


class ClaudeProvider(LLMProvider):
    name = "claude"

    async def complete(self, prompt: str, system: str, max_tokens: int = 500) -> str:
        import anthropic

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")

        client = anthropic.AsyncAnthropic(api_key=api_key)
        message = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text.strip()


class OpenAIProvider(LLMProvider):
    name = "openai"

    async def complete(self, prompt: str, system: str, max_tokens: int = 500) -> str:
        import openai

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY not set")

        client = openai.AsyncOpenAI(api_key=api_key)
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=max_tokens,
            temperature=0.1,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        )
        return response.choices[0].message.content.strip()


class AllProvidersExhausted(Exception):
    pass


# Build provider chain from whichever keys are configured.
# Called lazily (inside complete_with_fallback) so that load_dotenv() in
# main.py has already run before we read the environment variables.
# The result is cached after the first call.
_cached_provider_chain: list[LLMProvider] | None = None

# Provider order matters: cheapest/fastest first, then fallbacks.
_PROVIDERSPECS: list[tuple[str, type[LLMProvider]]] = [
    ("GROQ_API_KEY", GroqProvider),
    ("ANTHROPIC_API_KEY", ClaudeProvider),
    ("OPENAI_API_KEY", OpenAIProvider),
]


def _build_provider_chain() -> list[LLMProvider]:
    """
    Build the provider chain from whichever API keys are present in the
    environment. Providers without a configured key are skipped rather than
    added-and-failed, so the first entry is always a usable provider.
    """
    chain = [cls() for env_var, cls in _PROVIDERSPECS if os.environ.get(env_var)]
    if not chain:
        # Never return an empty chain: callers expect at least one provider, and
        # attempting the primary yields a precise "GROQ_API_KEY not set" error
        # rather than a vague "nothing configured".
        logger.error(
            "no_llm_providers_configured",
            extra={"checked": [name for name, _ in _PROVIDERSPECS]},
        )
        return [GroqProvider()]
    logger.info("llm_provider_chain_built", extra={"providers": [p.name for p in chain]})
    return chain


def _get_provider_chain() -> list[LLMProvider]:
    """Return (and cache) the provider chain built from current env vars."""
    global _cached_provider_chain, PROVIDER_CHAIN
    if _cached_provider_chain is not None:
        return _cached_provider_chain
    _cached_provider_chain = _build_provider_chain()
    # Keep the legacy module-level name in sync for callers that read it.
    PROVIDER_CHAIN = _cached_provider_chain
    return _cached_provider_chain


def reset_provider_chain() -> None:
    """Clear the cached chain — used by tests and after env changes."""
    global _cached_provider_chain, PROVIDER_CHAIN
    _cached_provider_chain = None
    PROVIDER_CHAIN = []


# Keep PROVIDER_CHAIN as a module-level name for backward compatibility
# (tests that patch it directly still work), but it is no longer used
# by complete_with_fallback directly.
PROVIDER_CHAIN: list[LLMProvider] = []  # populated lazily on first call


async def complete_with_fallback(
    prompt: str,
    system: str,
    max_tokens: int = 500,
    providers: list[LLMProvider] | None = None,
) -> tuple[str, str]:
    """
    Try each provider in order. Return (response_text, provider_name).
    Raises AllProvidersExhausted if every provider fails.

    The provider chain is built lazily on the first call so that
    load_dotenv() in main.py has already populated the environment before
    we inspect the API key variables.
    """
    chain = providers or _get_provider_chain()
    if not chain:
        raise AllProvidersExhausted(
            "No LLM provider is configured. Set at least one of "
            "GROQ_API_KEY, ANTHROPIC_API_KEY, or OPENAI_API_KEY."
        )
    last_exc: Exception | None = None

    for provider in chain:
        try:
            text = await provider.complete(prompt, system, max_tokens=max_tokens)
            if provider.name != (chain[0].name if chain else "groq"):
                logger.warning(
                    "llm_fallback_used",
                    extra={"provider": provider.name, "reason": str(last_exc)[:80]},
                )
            return text, provider.name
        except Exception as exc:
            logger.warning(
                "llm_provider_failed",
                extra={"provider": provider.name, "error": str(exc)[:120]},
            )
            last_exc = exc
            continue

    raise AllProvidersExhausted(
        f"All LLM providers exhausted. Last error: {last_exc}"
    )
