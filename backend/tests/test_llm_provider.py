"""
Tests for the multi-provider LLM abstraction (IDEA 06).
Uses lightweight fakes — no real API keys required.
"""
import pytest
from services.llm.llm_provider import (
    LLMProvider,
    AllProvidersExhausted,
    complete_with_fallback,
)


class OkProvider(LLMProvider):
    name = "ok_provider"
    async def complete(self, prompt, system, max_tokens=500):
        return '{"event_type": "payment_received", "confidence": 0.9}'


class FailProvider(LLMProvider):
    name = "fail_provider"
    async def complete(self, prompt, system, max_tokens=500):
        raise RuntimeError("quota exhausted")


class QuotaProvider(LLMProvider):
    """Simulates a quota error (HTTP 429 from any provider)."""
    name = "quota_provider"
    async def complete(self, prompt, system, max_tokens=500):
        raise RuntimeError("resource_exhausted: quota exceeded")


@pytest.mark.asyncio
async def test_first_provider_succeeds():
    text, provider = await complete_with_fallback(
        prompt="test", system="sys", providers=[OkProvider()]
    )
    assert provider == "ok_provider"
    assert "payment_received" in text


@pytest.mark.asyncio
async def test_falls_back_to_second_provider_on_failure():
    text, provider = await complete_with_fallback(
        prompt="test", system="sys",
        providers=[FailProvider(), OkProvider()],
    )
    assert provider == "ok_provider"


@pytest.mark.asyncio
async def test_falls_back_on_quota_error():
    text, provider = await complete_with_fallback(
        prompt="test", system="sys",
        providers=[QuotaProvider(), OkProvider()],
    )
    assert provider == "ok_provider"


@pytest.mark.asyncio
async def test_raises_when_all_providers_exhausted():
    with pytest.raises(AllProvidersExhausted):
        await complete_with_fallback(
            prompt="test", system="sys",
            providers=[FailProvider(), FailProvider()],
        )


@pytest.mark.asyncio
async def test_returns_first_provider_name_on_success():
    """When first provider works, no fallback used."""
    text, provider = await complete_with_fallback(
        prompt="hello", system="sys",
        providers=[OkProvider(), FailProvider()],
    )
    assert provider == "ok_provider"


# ── Lazy chain tests (Fix: PROVIDER_CHAIN was built at import time) ────────────

def test_provider_chain_not_built_at_import():
    """
    PROVIDER_CHAIN must be empty at module level so load_dotenv() in
    main.py runs before the provider env vars are read.
    _get_provider_chain() populates _cached_provider_chain lazily.
    """
    from services.llm import llm_provider
    # The module-level sentinel list should be empty (or falsy)
    assert llm_provider.PROVIDER_CHAIN == [], (
        "PROVIDER_CHAIN should be an empty sentinel at import time; "
        "provider selection must be deferred until first complete_with_fallback() call"
    )


def test_get_provider_chain_returns_a_list():
    """_get_provider_chain() must always return a non-empty list."""
    from services.llm.llm_provider import _get_provider_chain, _cached_provider_chain
    import services.llm.llm_provider as mod
    # Reset cache so we test the build path
    mod._cached_provider_chain = None
    chain = _get_provider_chain()
    assert isinstance(chain, list)
    assert len(chain) >= 1  # At least the Groq fallback is always added
