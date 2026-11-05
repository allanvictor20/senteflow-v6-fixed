"""
SenteFlow AI — EventExtractor (v5)
====================================
Two key upgrades:

IDEA 06 — Multi-Provider LLM Abstraction (P1):
  _call_llm() now uses complete_with_fallback() instead of a hardcoded provider.
  Chain: Groq → Claude → OpenAI. Owner sees no disruption if Groq quota hits.

IDEA 08 — Business Profile Memory (P1):
  build_system_prompt() injects per-org BusinessProfile into every LLM call.
  Groq now knows THIS business's products, credit policy, and "the usual".

All v4 fixes retained:
  - Amount coercion (_parse_amount, _coerce_entities)
  - Memory section injection (Gap 4)
  - Business context section (_build_business_context_section)
  - Confidence clamping, event_type validation, list coercion
"""

import json
import logging
import re
from typing import Any, Optional

from core.errors import async_with_retry
from domain.events.business_event import BusinessEvent
from domain.events.event_types import EventType

logger = logging.getLogger(__name__)

_NUMERIC_ENTITY_KEYS = frozenset({
    "amount", "quantity", "balance", "price", "total",
    "unit_price", "outstanding", "paid", "owed",
    "target_amount", "new_amount",
})


# ─── Amount normalisation ─────────────────────────────────────────────────────

def _parse_amount(raw) -> Optional[float]:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip()
    if not s:
        return None
    s = re.sub(r"^[A-Za-z]{1,4}\s*", "", s).strip()
    s = s.replace(",", "")
    k_match = re.match(r"^(\d+(?:\.\d+)?)[kK]$", s)
    if k_match:
        return float(k_match.group(1)) * 1_000
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _coerce_entities(raw_entities: Any) -> dict[str, Any]:
    if not isinstance(raw_entities, dict):
        logger.warning("entities_not_dict", extra={"got": type(raw_entities).__name__})
        return {}
    coerced: dict[str, Any] = {}
    for key, value in raw_entities.items():
        if key in _NUMERIC_ENTITY_KEYS:
            parsed = _parse_amount(value)
            if parsed is not None:
                coerced[key] = parsed
            else:
                logger.warning("entity_amount_unparseable", extra={"key": key, "raw": str(value)[:40]})
                coerced[key] = value
        else:
            coerced[key] = value
    return coerced


# ─── Base system prompt ───────────────────────────────────────────────────────

_BASE_SYSTEM_PROMPT = """You are a business intelligence engine for Ugandan SME owners using WhatsApp.
The person messaging you is the BUSINESS OWNER (or their staff). They tell you,
in their own words, what happened with their customers, suppliers and stock.
They are never the customer themselves. Your job is to extract structured
business meaning from casual, often code-switched messages.

Language notes:
- Luganda: "akuliwa"=received, "ssente"=money, "obuuza"=goods, "okulipa"=to pay, "nkuweereza"=I'm sending
- Swahili: "kulipa"=to pay, "mkopo"=loan, "pesa"=money, "mali"=goods
- "k" suffix = thousands (50k = 50,000 UGX)
- "remaining with" = current stock level

IMPORTANT — amounts:
- Always store amounts as plain numbers (50000, not "50k" or "UGX 50,000")
- If you cannot determine the exact amount, omit the "amount" key entirely

IMPORTANT — people:
- Put the customer (the other party the owner is talking about) in "customer",
  using the name exactly as the owner wrote it. Also fill the role key that fits:
  "payer" for payments received, "debtor" for debts and promises, "supplier"
  for suppliers. If the owner gives a phone number for them, put it in "phone".

IMPORTANT — dates:
- Write every date (e.g. "due_date") as YYYY-MM-DD, resolving words like
  "Friday", "tomorrow" or "end of month" against today's date given below.

Always return valid JSON matching the schema. Use "unknown" event_type if you cannot classify.
Set confidence between 0.0 (total guess) and 1.0 (certain).
"""

_USER_PROMPT_TEMPLATE = """Today is {today} ({weekday}) in Uganda.

Extract the business event from this WhatsApp message from the business owner.

{message_section}

{memory_section}{business_context_section}Full context:
{context_str}

Return a JSON object with:
- event_type: one of customer_inquiry, customer_order, order_received, negotiation,
  complaint, appointment_request, payment_received, payment_promise, debt_created,
  expense_recorded, delivery_update, inventory_update, low_stock_alert,
  supplier_message, business_note, follow_up_required, reminder_request,
  business_query, correction, unknown
  (use business_query when the owner is ASKING about their records, e.g.
  "who owes me?", "how much did Brian pay?", "send me a summary")
  (use correction when the owner says something ALREADY RECORDED is wrong or
  should be removed, e.g. "undo", "delete the last one", "Brian paid 40k not
  50k", "it was Kato not Brian", "Brian will pay Monday not Friday", "that
  was an expense, not a payment")
- confidence: 0.0–1.0
- entities: relevant key-value facts (amount as a plain number, customer, payer,
  debtor, item, quantity, due_date as YYYY-MM-DD, phone, etc.). For business_query
  also set "query_type" to one of summary, recent, debts, customer_balance and,
  for customer_balance, "customer".
  For correction set "correction_action" to "undo" (remove the record) or
  "change" (fix it). Describe the record being corrected with only what the
  owner said: "target_customer", "target_amount", "target_type" (one of
  payment, debt, expense, promise, order, stock). For "change" give the new
  values: "new_amount", "new_customer", "new_due_date" (YYYY-MM-DD),
  "new_type" (payment, debt, expense or promise), "new_item".
  Example: "Brian paid 40k not 50k" → correction_action "change",
  target_customer "Brian", target_type "payment", target_amount 50000,
  new_amount 40000.
- reasoning: one sentence explaining your classification
- recommended_actions: list of action keys you think apply (informational only)
- operational_effects: human-readable list of what should happen

IMPORTANT: Return ONLY JSON, no markdown fences."""


# ─── IDEA 08: Business profile injection ──────────────────────────────────────

async def _build_system_prompt(org_id: str, profile_repo=None) -> str:
    """
    IDEA 08: Inject per-org BusinessProfile into the system prompt.
    If no profile repo or no profile exists, returns the base prompt unchanged.
    """
    if profile_repo is None:
        return _BASE_SYSTEM_PROMPT

    try:
        profile = await profile_repo.get_profile(org_id)
        if profile:
            profile_section = profile.to_system_prompt_section()
            if profile_section:
                return _BASE_SYSTEM_PROMPT + profile_section
    except Exception as exc:
        logger.warning("business_profile_fetch_failed", extra={"error": str(exc), "org_id": org_id})

    return _BASE_SYSTEM_PROMPT


# ─── Context section builders ─────────────────────────────────────────────────

def _build_memory_section(context: dict[str, Any]) -> str:
    memory_str = context.get("recent_memory", "").strip()
    if not memory_str or memory_str == "No prior interactions.":
        return ""
    return (
        "Recent conversation history "
        "(use this to resolve pronouns like 'he'/'she' and references like 'the usual'):\n"
        f"{memory_str}\n\n"
    )


def _build_business_context_section(context: dict[str, Any]) -> str:
    lines: list[str] = []

    # Use CustomerMemory summary if available (IDEA 03 benefit visible here too)
    summary = context.get("customer", {}).get("summary")
    if summary:
        lines.append(f"Customer summary: {summary}")
    else:
        customer = context.get("customer_profile") or context.get("customer") or {}
        if customer:
            name = customer.get("display_name") or customer.get("name")
            if name:
                lines.append(f"Customer: {name}")
            balance = customer.get("outstanding_balance") or customer.get("last_amount")
            if balance:
                try:
                    lines.append(f"Outstanding balance: UGX {float(balance):,.0f}")
                except (TypeError, ValueError):
                    lines.append(f"Outstanding balance: {balance}")
            last_item = customer.get("last_item")
            if last_item:
                lines.append(f"Last item purchased: {last_item}")

    recent_txns = context.get("recent_transactions") or []
    if recent_txns:
        recent_amounts = []
        for txn in recent_txns[:3]:
            amt = txn.get("amount")
            item = txn.get("description") or txn.get("item")
            if amt:
                try:
                    entry = f"UGX {float(amt):,.0f}"
                    if item:
                        entry += f" ({item})"
                    recent_amounts.append(entry)
                except (TypeError, ValueError):
                    pass
        if recent_amounts:
            lines.append(f"Recent transaction amounts: {', '.join(recent_amounts)}")

    if not lines:
        return ""
    return "Business-specific context:\n" + "\n".join(f"- {l}" for l in lines) + "\n\n"


_MAX_CONTEXT_CHARS = 4000


def _build_context_str(context: dict[str, Any]) -> str:
    skip_keys = {"recent_memory", "raw_interactions", "customer_profile", "recent_transactions", "wa_client"}
    stripped = {k: v for k, v in context.items() if k not in skip_keys and v not in ({}, [], None, "")}
    if not stripped:
        return "None"
    text = json.dumps(stripped, indent=2, default=str)
    if len(text) > _MAX_CONTEXT_CHARS:
        text = text[:_MAX_CONTEXT_CHARS] + "\n... (truncated)"
    return text


def _build_message_section(message: str, translation: Optional[dict[str, Any]]) -> str:
    """The owner's message, plus Sunbird's English translation when there is one."""
    if translation and translation.get("translated") and translation.get("english_text"):
        language = translation.get("language_name") or translation.get("language") or "a local language"
        return (
            f'Message (original, {language}): "{message}"\n'
            f'English translation (machine translation — it may contain mistakes; '
            f'when it disagrees with the original about names or amounts, trust the '
            f'original): "{translation["english_text"]}"'
        )
    if translation and translation.get("language") not in (None, "", "eng"):
        language = translation.get("language_name") or translation.get("language")
        return f'Message (written in {language}; no translation available): "{message}"'
    return f'Message: "{message}"'


def _today_local():
    """Today's date in the business time zone (BUSINESS_TZ, default Africa/Kampala)."""
    from utils.clock import local_now
    return local_now()


# ─── Main entry point ─────────────────────────────────────────────────────────

async def extract_event(
    raw_message: str,
    sender_id: str,
    business_id: str,
    context: Optional[dict[str, Any]] = None,
    profile_repo=None,
    translation: Optional[dict[str, Any]] = None,
) -> BusinessEvent:
    """
    Main entry point: convert a raw WhatsApp message into a BusinessEvent.

    Args:
        raw_message:  The raw text from the WhatsApp message.
        sender_id:    WhatsApp sender phone/ID.
        business_id:  The org/business identifier.
        context:      Enrichment dict from ContextEngine.
        profile_repo: BusinessProfileRepository — for IDEA 08 prompt injection.
        translation:  normalize_business_text() result for non-English messages.

    Returns:
        A populated BusinessEvent (even on failure — type=UNKNOWN, confidence=0).
    """
    event = BusinessEvent(
        raw_message=raw_message,
        sender_id=sender_id,
        business_id=business_id,
        context=context or {},
    )
    if translation and translation.get("language") not in (None, "eng"):
        event.language = translation.get("language")
        if translation.get("translated"):
            event.translated_text = translation.get("english_text")

    try:
        extra = {"translation": translation} if translation and translation.get("language") not in (None, "eng") else {}
        extracted = await _call_llm(raw_message, context or {}, business_id, profile_repo, **extra)
        _apply_extraction(event, extracted)
    except Exception as exc:
        logger.error("event_extraction_failed", extra={"error": str(exc), "raw_message": raw_message[:80]})
        event.event_type = EventType.UNKNOWN
        event.confidence = 0.0
        event.reasoning = f"Extraction failed: {exc}"

    return event


# ─── IDEA 06: provider-agnostic LLM call ─────────────────────────────────────

@async_with_retry(max_attempts=3, delay_seconds=1.5)
async def _call_llm(
    message: str,
    context: dict[str, Any],
    org_id: str = "default",
    profile_repo=None,
    translation: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    IDEA 06: Call LLM with fallback chain instead of a hardcoded provider.
    Uses complete_with_fallback() → Groq → Claude → OpenAI.
    """
    from services.llm.llm_provider import complete_with_fallback

    system_prompt = await _build_system_prompt(org_id, profile_repo)

    memory_section = _build_memory_section(context)
    business_context_section = _build_business_context_section(context)
    context_str = _build_context_str(context)

    today = _today_local()
    user_prompt = _USER_PROMPT_TEMPLATE.format(
        today=today.strftime("%Y-%m-%d"),
        weekday=today.strftime("%A"),
        message_section=_build_message_section(message, translation),
        memory_section=memory_section,
        business_context_section=business_context_section,
        context_str=context_str,
    )

    raw, provider_used = await complete_with_fallback(
        prompt=user_prompt,
        system=system_prompt,
        max_tokens=1500,
        json_mode=True,
    )

    if provider_used != "groq":
        logger.info("extraction_used_fallback_provider", extra={"provider": provider_used})

    from services.llm.models import clean_completion
    raw = clean_completion(raw)

    parsed = json.loads(raw)

    if not isinstance(parsed, dict):
        raise ValueError(f"LLM returned non-dict JSON: {type(parsed).__name__}")

    if "entities" in parsed:
        parsed["entities"] = _coerce_entities(parsed["entities"])

    try:
        parsed["confidence"] = max(0.0, min(1.0, float(parsed.get("confidence", 0.0))))
    except (TypeError, ValueError):
        parsed["confidence"] = 0.0

    raw_type = parsed.get("event_type", "unknown")
    valid_values = {e.value for e in EventType}
    if raw_type not in valid_values:
        # NB: "message" is a reserved LogRecord attribute — putting it in
        # `extra` raises KeyError inside logging itself.
        logger.warning("llm_unknown_event_type", extra={"raw": raw_type, "raw_message": message[:60]})
        parsed["event_type"] = "unknown"

    for list_key in ("recommended_actions", "operational_effects"):
        if not isinstance(parsed.get(list_key), list):
            parsed[list_key] = []

    return parsed


def _apply_extraction(event: BusinessEvent, data: dict[str, Any]) -> None:
    raw_type = data.get("event_type", "unknown")
    try:
        event.event_type = EventType(raw_type)
    except ValueError:
        event.event_type = EventType.UNKNOWN

    try:
        event.confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
    except (TypeError, ValueError):
        event.confidence = 0.0

    event.entities = data.get("entities", {}) or {}
    event.reasoning = data.get("reasoning", "")
    # The model's action suggestions are kept for audit only. Which tools run
    # is decided deterministically by event type in the ActionDispatcher —
    # letting the model pick meant orders were rarely created and unknown
    # keys (e.g. "record_expense") failed silently.
    event.suggested_actions = [str(a) for a in data.get("recommended_actions", []) if a]
    event.recommended_actions = []
    event.operational_effects = data.get("operational_effects", [])

    logger.info(
        "event_extracted",
        extra={
            "type": event.event_type.value,
            "confidence": event.confidence,
            "sender": event.sender_id,
            "amount": event.entities.get("amount"),
        },
    )
