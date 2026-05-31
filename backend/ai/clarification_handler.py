"""Clarification prompts for low-confidence conversational inputs."""

from ai.intent_classifier import IntentResult


def needs_clarification(
    intent: IntentResult,
    extraction_confidence: float | None = None,
) -> bool:
    score = extraction_confidence if extraction_confidence is not None else intent.confidence
    return score < 0.7


def build_clarification_question(
    text: str,
    last_interactions: list[dict] | None = None,
) -> str:
    lowered = (text or "").lower()

    if "usual" in lowered and last_interactions:
        for item in last_interactions:
            extracted = item.get("extracted") or {}
            amount = extracted.get("amount")
            currency = extracted.get("currency", "UGX")
            person = extracted.get("payer") or extracted.get("payee")
            if amount:
                person_str = f" from {person}" if person else ""
                return (
                    f"Do you mean {currency} {float(amount):,.0f}{person_str}"
                    " - same as last time?"
                )

    pronoun_triggers = (
        "he paid",
        "she paid",
        "they paid",
        "he sent",
        "she sent",
        "he owes",
        "she owes",
        "he cleared",
        "she cleared",
    )
    if any(p in lowered for p in pronoun_triggers) and last_interactions:
        last = last_interactions[0]
        extracted = last.get("extracted") or {}
        person = extracted.get("payer") or extracted.get("payee")
        if person:
            return f"Are you referring to {person}?"

    return (
        "I couldn't identify the amount clearly. "
        "Please send it like: Brian paid UGX 50,000 for feed."
    )
