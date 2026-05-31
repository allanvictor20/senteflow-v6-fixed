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
            gemini_text = await _gemini_clarification(data, confidence, original_text)
        except Exception as exc:
            logger.warning("gemini_clarification_failed", extra={"error": str(exc)})
            gemini_text = (
                "I wasn't completely sure about that. "
                "Could you confirm the amount and person?"
            )
        return AssistantReply(
            text=gemini_text,
            is_clarification=True,
            confidence=confidence,
        )

    return AssistantReply(
        text=confirmation(data, confidence),
        is_clarification=False,
        confidence=confidence,
    )


async def _gemini_clarification(
    data: dict,
    confidence: float,
    original_text: str,
) -> str:
    """Use Gemini to generate a natural clarification question."""
    from google import genai
    from google.genai.types import GenerateContentConfig

    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    prompt = (
        f'The user sent: "{original_text}"\n'
        f"Extracted so far (confidence {confidence:.0%}): {json.dumps(data)}\n\n"
        "Write a SHORT WhatsApp reply (1-2 lines) that:\n"
        "- Confirms what you understood\n"
        "- Asks ONE specific question about what was unclear\n"
        "- Sounds like a helpful assistant, not a form\n"
        "Return ONLY the reply text."
    )
    import asyncio as _asyncio
    response = await _asyncio.to_thread(
        client.models.generate_content,
        model="gemini-2.0-flash",
        contents=prompt,
        config=GenerateContentConfig(max_output_tokens=100, temperature=0.3),
    )
    return response.text.strip()
