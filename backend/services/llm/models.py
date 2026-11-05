"""
SenteFlow — LLM model configuration
====================================
One place that decides which Groq model each call uses.

Groq retired the models SenteFlow originally shipped with (free/dev tier):
  - llama-3.2-90b-vision-preview   → retired 2025-04-14
  - meta-llama/llama-4-scout-...   → retired 2026-07-17
  - llama-3.3-70b-versatile        → retired 2026-08-16
See https://console.groq.com/docs/deprecations

Defaults are now:
  text   → openai/gpt-oss-120b   (JSON mode, text only)
  vision → qwen/qwen3.8-27b      (multimodal, JSON mode)
  stt    → whisper-large-v3      (still current)

GROQ_MODEL / GROQ_VISION_MODEL / GROQ_WHISPER_MODEL still override the
defaults. If an override names a model we know is retired, we log a warning
and use the current default instead, so a stale .env cannot silently take the
whole assistant offline.
"""

from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)

GROQ_BASE_URL = "https://api.groq.com/openai/v1"

DEFAULT_TEXT_MODEL = "openai/gpt-oss-120b"
DEFAULT_VISION_MODEL = "qwen/qwen3.8-27b"
DEFAULT_WHISPER_MODEL = "whisper-large-v3"

# Fallback providers (only used when their API keys are set).
DEFAULT_ANTHROPIC_MODEL = "claude-haiku-5-5"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"

_RETIRED_TEXT_MODELS = {
    "llama-3.3-70b-versatile",
    "llama-3.1-70b-versatile",
    "llama-3.1-8b-instant",
    "llama3-70b-8192",
    "llama3-8b-8192",
    "qwen/qwen3-32b",
    "qwen/qwen3.6-27b",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "mixtral-8x7b-32768",
    "gemma2-9b-it",
}
_RETIRED_VISION_MODELS = {
    "llama-3.2-90b-vision-preview",
    "llama-3.2-11b-vision-preview",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "qwen/qwen3.6-27b",
}

_warned: set[str] = set()


def _pick(env_var: str, default: str, retired: set[str]) -> str:
    value = (os.environ.get(env_var) or "").strip()
    if not value:
        return default
    if value in retired:
        if value not in _warned:
            _warned.add(value)
            logger.warning(
                "groq_model_retired_using_default",
                extra={"env_var": env_var, "configured": value, "using": default},
            )
        return default
    return value


def text_model() -> str:
    return _pick("GROQ_MODEL", DEFAULT_TEXT_MODEL, _RETIRED_TEXT_MODELS)


def vision_model() -> str:
    return _pick("GROQ_VISION_MODEL", DEFAULT_VISION_MODEL, _RETIRED_VISION_MODELS)


def whisper_model() -> str:
    return (os.environ.get("GROQ_WHISPER_MODEL") or "").strip() or DEFAULT_WHISPER_MODEL


def anthropic_model() -> str:
    return (os.environ.get("ANTHROPIC_MODEL") or "").strip() or DEFAULT_ANTHROPIC_MODEL


def openai_model() -> str:
    return (os.environ.get("OPENAI_MODEL") or "").strip() or DEFAULT_OPENAI_MODEL


def groq_extra_body(model: str) -> dict:
    """
    Keep reasoning models from spending the token budget on thinking or
    leaking it into the answer.

    - gpt-oss models: low reasoning effort (reasoning is returned separately).
    - Qwen 3.x models: instruct mode (reasoning_effort="none").
    """
    if model.startswith("openai/gpt-oss"):
        return {"reasoning_effort": "low"}
    if model.startswith("qwen/"):
        return {"reasoning_effort": "none"}
    return {}


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def clean_completion(text: str | None) -> str:
    """Strip reasoning tags and markdown fences from a model reply."""
    raw = _THINK_RE.sub("", text or "").strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        if len(parts) >= 2:
            raw = parts[1]
            if raw.lstrip().lower().startswith("json"):
                raw = raw.lstrip()[4:]
        raw = raw.strip()
    return raw
