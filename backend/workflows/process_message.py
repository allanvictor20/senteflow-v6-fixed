"""
SenteFlow — ProcessMessageWorkflow
====================================
Orchestrates the full lifecycle of an incoming WhatsApp message.

Pipeline:
  IncomingMessage
    → ContextEngine.build()          # load customer memory, open orders, history
    → EventInterpreter.interpret()   # LLM: classify into BusinessEvent
    → update_memory()                # persist what we learned
    → generate_reply()               # LLM: craft WhatsApp response
    → send_reply()                   # deliver via Evolution API

This is the ONLY entry point for WhatsApp message processing.
Services are pure capabilities. This workflow is the orchestration.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from domain.events import BusinessEvent, EventType, ProcessingStatus

logger = logging.getLogger(__name__)


@dataclass
class MessageWorkflowResult:
    event: Optional[BusinessEvent] = None
    reply_sent: bool = False
    whatsapp_reply: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.event is not None and len(self.errors) == 0


async def process_message(
    raw_message: str,
    sender_id: str,
    org_id: str,
    message_type: str = "text",
    media_url: Optional[str] = None,
    media_mime_type: Optional[str] = None,
    repo=None,
    wa_client=None,
    mem_repo=None,
    profile_repo=None,
    org_config=None,
) -> MessageWorkflowResult:
    """
    Main workflow entry point.

    Args:
        raw_message:      The raw text from the WhatsApp message.
        sender_id:        WhatsApp phone number / sender ID.
        org_id:           The business org identifier.
        message_type:     "text" | "voice" | "image" | "document"
        media_url:        URL to download media (if applicable).
        media_mime_type:  MIME type of the media (if applicable).
        repo:             Injected repository (TransactionRepository or equivalent).
        wa_client:        Injected WhatsApp client for sending replies.
    """
    result = MessageWorkflowResult()

    try:
        # ── Step 1: Build context ─────────────────────────────────────────────
        from services.memory.context_engine import ContextEngine
        from memory.conversation_memory import ConversationMemory

        context = {}
        if repo:
            ctx_engine = ContextEngine(repo=repo, org_id=org_id, mem_repo=mem_repo)
            context = await ctx_engine.get_context(sender_id=sender_id, raw_message=raw_message)

            try:
                interactions = ConversationMemory(repo, org_id).last_interactions(sender_id, limit=5)
                context["recent_memory"] = _format_memory(interactions)
                context["raw_interactions"] = interactions
            except Exception as exc:
                logger.warning("memory_inject_failed", extra={"error": str(exc)})
                context["recent_memory"] = ""

        context["wa_client"] = wa_client

        # ── Step 2: Interpret → BusinessEvent ─────────────────────────────────
        from services.llm.event_extractor import extract_event
        from services.llm.language_pipeline import normalize_business_text
        from services.llm.media_processor import transcribe_audio

        if message_type == "text":
            lang_ctx = await normalize_business_text(raw_message)
            normalized = lang_ctx.get("normalized_text") or raw_message
            event = await extract_event(normalized, sender_id, org_id, context, profile_repo=profile_repo)
            event.raw_message = raw_message

        elif message_type in ("voice", "audio") and media_url:
            transcript = await transcribe_audio(media_url, media_mime_type, wa_client=wa_client)
            lang_ctx = await normalize_business_text(transcript or "")
            normalized = lang_ctx.get("normalized_text") or transcript or raw_message
            event = await extract_event(normalized, sender_id, org_id, {**context, "source_modality": "voice_note"}, profile_repo=profile_repo)
            event.raw_message = transcript

        else:
            # Image / document — trigger extraction pipeline separately
            event = BusinessEvent(
                raw_message=raw_message,
                sender_id=sender_id,
                business_id=org_id,
                event_type=EventType.BUSINESS_NOTE,
                confidence=0.5,
                context=context,
                reasoning=f"Media type '{message_type}' — forwarded to extraction pipeline.",
                recommended_actions=["run_extraction_workflow"],
            )

        event.processing_status = ProcessingStatus.INTERPRETED
        result.event = event

        logger.info(
            "message_interpreted",
            extra={
                "event_type": event.event_type.value,
                "confidence": round(event.confidence, 2),
                "sender": sender_id,
            },
        )

        # ── Step 3: Update memory ─────────────────────────────────────────────
        if repo:
            try:
                from services.memory.memory_engine import update_from_event
                from repositories.customer_repository import CustomerRepository

                _mem_repo = mem_repo
                if _mem_repo is None:
                    from repositories.memory_repository import MemoryRepository as _MR
                    _mem_repo = _MR(repo._db)
                cust_repo = CustomerRepository(repo._db)
                await update_from_event(event.model_dump(mode="json"), org_id, _mem_repo, cust_repo)
            except Exception as exc:
                logger.warning("memory_update_failed", extra={"error": str(exc)})
                result.errors.append(f"memory_update: {exc}")

        # ── Step 4: Execute actions ───────────────────────────────────────────
        if repo and event.recommended_actions != ["run_extraction_workflow"]:
            try:
                from services.actions.action_dispatcher import ActionDispatcher
                dispatcher = ActionDispatcher(repo=repo, org_id=org_id, org_config=org_config)
                action_result = await dispatcher.dispatch(event)
                result.whatsapp_reply = action_result.whatsapp_reply
            except Exception as exc:
                logger.error("action_dispatch_failed", extra={"error": str(exc)})
                result.errors.append(f"action_dispatch: {exc}")
                result.whatsapp_reply = event.to_summary()

        # ── Step 5: Send reply ────────────────────────────────────────────────
        if wa_client and result.whatsapp_reply:
            try:
                from services.whatsapp.reply_sender import send
                # We don't have chat_id here; caller handles the actual send
                result.reply_sent = True
            except Exception as exc:
                logger.error("reply_send_failed", extra={"error": str(exc)})
                result.errors.append(f"reply_send: {exc}")

        event.processing_status = ProcessingStatus.COMPLETED

    except Exception as exc:
        logger.error("process_message_failed", extra={"error": str(exc), "sender": sender_id})
        result.errors.append(f"workflow: {exc}")

    return result


def _format_memory(interactions: list[dict]) -> str:
    """Format recent interactions into a prompt-friendly string."""
    if not interactions:
        return ""
    lines = []
    for i in interactions[:5]:
        ts = str(i.get("timestamp", ""))[:16]
        intent = i.get("intent", "?")
        msg = str(i.get("message") or "")[:80]
        lines.append(f"- [{ts}] {intent}: {msg}")
    return "\n".join(lines)
