"""Conversational reply helpers for the assistant layer."""

import json
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class AssistantReply:
    text: str
    is_clarification: bool = False
    confidence: float = 1.0


def confirmation(data: dict, confidence: float | None = None) -> str:
    name = data.get("payer") or data.get("payee") or data.get("customer") or "Customer"
    amount = data.get("amount")
    currency = data.get("currency", "UGX")
    category = data.get("category") or "business activity"
    lines = ["Recorded successfully.", "", f"Customer: {name}"]
    if amount is not None:
        lines.append(f"Amount: {currency} {float(amount):,.0f}")
    lines.append(f"Category: {str(category).replace('_', ' ').title()}")
    if confidence is not None and confidence < 0.85:
        lines.append(f"Confidence: {confidence:.0%} - let me know if anything looks wrong.")
    return "\n".join(lines)


def error(message: str = "I could not process that clearly.") -> str:
    return f"{message}\nPlease send a clearer message, receipt photo, or voice note."


def summary(lines: list[str]) -> str:
    if not lines:
        return "No business activity recorded yet."
    return "Here is what I remember:\n\n" + "\n".join(lines)


async def generate_smart_confirmation(
    data: dict,
    confidence: float,
    original_text: str = "",
) -> AssistantReply:
    """Return a typed confirmation or clarification reply."""
    if confidence >= 0.85:
        return AssistantReply(
            text=confirmation(data, confidence),
            is_clarification=False,
            confidence=confidence,
        )

    if confidence < 0.75:
        try:
            groq_text = await _groq_clarification(data, confidence, original_text)
        except Exception as exc:
            logger.warning("groq_clarification_failed", extra={"error": str(exc)})
            groq_text = (
                "I wasn't completely sure about that. "
                "Could you confirm the amount and person?"
            )
        return AssistantReply(
            text=groq_text,
            is_clarification=True,
            confidence=confidence,
        )

    return AssistantReply(
        text=confirmation(data, confidence),
        is_clarification=False,
        confidence=confidence,
    )


async def _groq_clarification(
    data: dict,
    confidence: float,
    original_text: str,
) -> str:
    """Use Groq to generate a natural clarification question."""
    from openai import AsyncOpenAI

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not set")
    client = AsyncOpenAI(
        api_key=api_key,
        base_url="https://api.groq.com/openai/v1",
    )
    model = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
    prompt = (
        f'The user sent: "{original_text}"\n'
        f"Extracted so far (confidence {confidence:.0%}): {json.dumps(data)}\n\n"
        "Write a SHORT WhatsApp reply (1-2 lines) that:\n"
        "- Confirms what you understood\n"
        "- Asks ONE specific question about what was unclear\n"
        "- Sounds like a helpful assistant, not a form\n"
        "Return ONLY the reply text."
    )
    response = await client.chat.completions.create(
        model=model,
        max_tokens=100,
        temperature=0.3,
        messages=[
            {"role": "system", "content": "You are a helpful WhatsApp business assistant."},
            {"role": "user", "content": prompt},
        ],
    )
    return (response.choices[0].message.content or "").strip()


# Backward-compatible alias
_gemini_clarification = _groq_clarification
