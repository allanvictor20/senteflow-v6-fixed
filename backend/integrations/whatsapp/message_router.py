"""
SenteFlow AI — Message Router (v7)
===================================
Routes one business owner's WhatsApp messages through the event pipeline.

Who is talking: every message here comes from a phone number linked to this
business (see services/tenancy.py) — the owner or their staff. Customers never
message SenteFlow; the owner tells SenteFlow about them ("Brian paid 50k").

Text flow:
  pending question? ── approval (YES/NO) → resume / cancel the paused event
                    ── reminder follow-up (YES) → mark settled
                    ── correction (YES/NO, or which number) → apply / leave
                    ── clarification → merge the answer and re-run
  → "undo" / "delete that"? → correction, no AI needed
  → language: detect, translate to English (Sunbird AI)
  → ContextEngine (owner-level context)
  → extract_event (LLM, original + translation) → BusinessEvent
  → correction? find the record, show before/after, wait for YES
  → business_query? answer from the records and stop
  → low-confidence money event? ask BEFORE recording anything
  → resolve the customer from the message (ask if ambiguous)
  → ActionDispatcher (may pause for approval or clarification)
  → reply, remember, update customer memory

Voice notes are transcribed and each utterance goes through the text flow
(so voice can do everything text can, including questions and reminders).
Receipt photos and documents go through the file extraction pipeline.
"""

import asyncio
import inspect
import logging
import re
from typing import TYPE_CHECKING, Optional

from core.message_event import MessageEvent, MessageType
from services.actions.action_dispatcher import ActionDispatcher
from services.llm.event_extractor import extract_event

if TYPE_CHECKING:
    from integrations.whatsapp.client import EvolutionClient
    from repositories.transaction_repository import TransactionRepository

logger = logging.getLogger(__name__)

# Money events below this confidence get a question before anything is saved.
CLARIFY_BELOW_CONFIDENCE = 0.75

# Fire-and-forget tasks must be strongly referenced, otherwise the event loop
# is free to garbage-collect them mid-flight and the work silently vanishes.
_background_tasks: set[asyncio.Task] = set()


def _spawn(coro, name: str) -> None:
    task = asyncio.create_task(coro, name=name)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


async def _repo_call(fn, *args, **kwargs):
    """Run a (usually synchronous) repository call off the event loop."""
    if inspect.iscoroutinefunction(fn):
        return await fn(*args, **kwargs)
    result = await asyncio.to_thread(fn, *args, **kwargs)
    return await _maybe_await(result)


def _mentions(text: str, terms: tuple[str, ...]) -> bool:
    """
    Whole-word containment test.

    Substring matching would fire the greeting branch on "this"/"shipping"
    (both contain "hi"), so every term is matched on word boundaries instead.
    """
    return any(re.search(rf"\b{re.escape(term)}\b", text) for term in terms)


_YES = {
    "yes", "y", "yeah", "yep", "yup", "ok", "okay", "sure", "confirm", "confirmed",
    "correct", "go ahead", "proceed", "paid", "settled", "done",
    "ye", "yee", "yego", "kale", "ndiyo", "sawa", "ego",
}
_NO = {
    "no", "n", "nope", "nah", "cancel", "stop", "don't", "dont", "not yet", "not paid",
    "nedda", "hapana", "si",
}


def _yes_no(text: str) -> Optional[bool]:
    """Interpret a short reply as yes/no. None when it is not a yes/no answer."""
    t = re.sub(r"[^\w\s']", " ", (text or "").lower()).strip()
    t = re.sub(r"\s+", " ", t)
    if not t or len(t.split()) > 4:
        return None
    if t in _YES or t.split()[0] in _YES:
        return True
    if t in _NO or t.split()[0] in _NO:
        return False
    return None


# ── Pending question state ────────────────────────────────────────────────────

class PendingStateManager:
    """
    Remembers that the bot asked the owner something and is waiting for the
    answer. Stored on the owner's conversation document (keyed by their
    WhatsApp id), with an in-memory fallback if Firestore is unavailable.

    pending_intent:
      awaiting_clarification  — pending_context.original_text
      awaiting_approval       — pending_context.event / action / completed
      awaiting_reminder_ack   — pending_context.reminder_ids
      awaiting_correction     — pending_context.plan (see services/corrections)
      awaiting_correction_choice — pending_context.choices / action / changes
    """

    def __init__(self, repo, org_id: str):
        self.repo = repo
        self.org_id = org_id
        self._pending_cache: dict[str, dict] = {}

    async def _set(self, sender_id: str, intent: str, context: dict, question: str) -> None:
        state = {
            "pending_intent": intent,
            "pending_context": context,
            "pending_question": question,
        }
        self._pending_cache[sender_id] = state
        try:
            await _repo_call(self.repo.upsert_conversation, self.org_id, sender_id, dict(state))
        except Exception as exc:
            logger.warning("pending_state_write_failed", extra={"error": str(exc)})

    async def set_pending_clarification(
        self,
        sender_id: str,
        original_text: str,
        clarification_question: str,
    ) -> None:
        await self._set(
            sender_id,
            "awaiting_clarification",
            {"original_text": original_text},
            clarification_question,
        )

    async def set_pending_approval(
        self,
        sender_id: str,
        event_data: dict,
        action: str,
        completed: list[str],
        question: str,
    ) -> None:
        await self._set(
            sender_id,
            "awaiting_approval",
            {"event": event_data, "action": action, "completed": list(completed)},
            question,
        )

    async def set_pending(self, sender_id: str, intent: str, context: dict, question: str) -> None:
        await self._set(sender_id, intent, context, question)

    async def set_pending_reminder_ack(self, sender_id: str, reminder_ids: list[str], question: str) -> None:
        await self._set(sender_id, "awaiting_reminder_ack", {"reminder_ids": list(reminder_ids)}, question)

    async def get_pending_state(self, sender_id: str) -> dict | None:
        """
        Firestore is the source of truth when it answers (another instance may
        have cleared or set the state). The in-memory copy is only used when
        the conversation document can't be read or doesn't exist yet.
        """
        try:
            conv = await _repo_call(self.repo.get_conversation, self.org_id, sender_id)
        except Exception as exc:
            logger.warning("pending_state_read_failed", extra={"error": str(exc)})
            conv = None
        if conv:
            return conv if conv.get("pending_intent") else None
        cached = self._pending_cache.get(sender_id)
        return cached if cached and cached.get("pending_intent") else None

    async def clear_pending(self, sender_id: str) -> None:
        self._pending_cache.pop(sender_id, None)
        try:
            await _repo_call(
                self.repo.upsert_conversation,
                self.org_id,
                sender_id,
                {"pending_intent": None, "pending_context": None, "pending_question": None},
            )
        except Exception as exc:
            logger.warning("pending_state_clear_failed", extra={"error": str(exc)})


# ── Router ─────────────────────────────────────────────────────────────────────

class MessageRouter:
    """Routes normalized MessageEvent objects for ONE business."""

    def __init__(
        self,
        wa_client: "EvolutionClient",
        repo: "TransactionRepository",
        org_id: str = "default",
        mem_repo=None,
        profile_repo=None,
        org_config=None,
        customer_memory_svc=None,
    ):
        self.wa_client = wa_client
        self.repo = repo
        self.org_id = org_id
        self.mem_repo = mem_repo
        self.profile_repo = profile_repo
        self.org_config = org_config
        self.customer_memory_svc = customer_memory_svc
        # Optional replacement for ActionDispatcher (kept for older wiring).
        self.event_pipeline = None
        # One manager per router, not per message: its in-memory fallback cache
        # is useless if a fresh instance is constructed on every turn.
        self.pending_mgr = PendingStateManager(repo, org_id)
        # Language each owner last wrote in (ISO 639-3) — used to translate
        # replies when the business asked for replies in the owner's language.
        self._owner_language: dict[str, str] = {}

    async def route(self, event: MessageEvent) -> None:
        logger.info(
            "routing_message",
            extra={"type": event.message_type.value, "sender": event.display_sender, "org_id": self.org_id},
        )

        match event.message_type:
            case MessageType.TEXT:
                await self._route_text(event)
            case MessageType.VOICE | MessageType.AUDIO:
                await self._route_voice(event)
            case MessageType.IMAGE | MessageType.DOCUMENT:
                await self._process_media_extraction(event, source_hint=event.message_type.value)
            case MessageType.VIDEO:
                await self._reply_unsupported(event, "video files")
            case MessageType.STICKER:
                pass
            case MessageType.LOCATION:
                await self._reply(event, "📍 Location noted. I can only process business records for now.")
            case _:
                await self._reply(
                    event,
                    "I received your message but couldn't understand the format. "
                    "Try sending a receipt photo, voice note, or text like \"Brian paid 50k for feed\".",
                )

    # ── Text ──────────────────────────────────────────────────────────────────

    async def _route_text(self, event: MessageEvent) -> None:
        if not event.text:
            return

        pending = await self.pending_mgr.get_pending_state(event.sender_id)
        intent = (pending or {}).get("pending_intent")

        if intent == "awaiting_approval":
            if await self._handle_approval_reply(event, pending):
                return
        elif intent == "awaiting_reminder_ack":
            if await self._handle_reminder_ack(event, pending):
                return
        elif intent == "awaiting_correction":
            if await self._handle_correction_reply(event, pending):
                return
        elif intent == "awaiting_correction_choice":
            if await self._handle_correction_choice(event, pending):
                return
        elif intent == "awaiting_clarification":
            original_text = (pending.get("pending_context") or {}).get("original_text", "")
            merged_text = f"{original_text} — {event.text}" if original_text else event.text
            logger.info(
                "resolving_pending_clarification",
                extra={"sender": event.sender_id, "merged": merged_text[:80]},
            )
            await self.pending_mgr.clear_pending(event.sender_id)
            # model_copy() is the correct way to copy a Pydantic model with overrides.
            merged_event = event.model_copy(update={"text": merged_text})
            await self._route_via_event_pipeline(merged_event, already_clarified=True)
            return

        await self._route_via_event_pipeline(event)

    # ── Voice ─────────────────────────────────────────────────────────────────

    async def _route_voice(self, event: MessageEvent) -> None:
        """Transcribe, then treat each utterance like a text message."""
        from integrations.whatsapp.media_handler import save_whatsapp_media
        from services.llm.media_processor import MediaDownloadError, transcribe_voice_note

        try:
            file_path = await save_whatsapp_media(event, self.wa_client)
        except MediaDownloadError as exc:
            logger.error("voice_download_failed", extra={"error": str(exc), "sender": event.sender_id})
            await self._reply(event, "⚠️ I couldn't download that voice note from WhatsApp. Please send it again.")
            return
        if not file_path:
            await self._reply(event, "⚠️ I couldn't access that voice note. Please send it again.")
            return

        try:
            settings = await self._settings()
            audio_bytes = await asyncio.to_thread(_read_bytes, file_path)
            utterances = await transcribe_voice_note(
                audio_bytes, event.media_mime_type, language=settings["language"]
            )
        except Exception as exc:
            logger.error("voice_transcription_failed", extra={"error": str(exc)})
            utterances = []

        if not utterances:
            await self._reply(event, "🎤 I couldn't make out that voice note. Could you say it again, or type it?")
            return

        for text in utterances:
            await self._route_text(event.model_copy(update={"text": text}))

    # ── Unified event pipeline ────────────────────────────────────────────────

    async def _route_via_event_pipeline(self, event: MessageEvent, already_clarified: bool = False) -> None:
        from services.memory.context_engine import ContextEngine
        from domain.events.event_types import EventType

        from services.corrections import is_undo_command

        try:
            # "undo" / "delete that" needs no AI (and works when it's down).
            if not already_clarified and is_undo_command(event.text or ""):
                await self._start_correction(event, "undo", {}, {})
                return

            translation = await self._normalize_language(event)

            # Owner-level context; the customer isn't known until extraction.
            context_engine = ContextEngine(repo=self.repo, org_id=self.org_id, mem_repo=self.mem_repo)
            context = await context_engine.get_context(
                sender_id=event.sender_id,
                raw_message=event.text or "",
            )

            try:
                from memory.conversation_memory import ConversationMemory
                interactions = await asyncio.to_thread(
                    ConversationMemory(self.repo, self.org_id).last_interactions, event.sender_id, 5
                )
                context["recent_memory"] = _format_memory_for_prompt(interactions)
                context["raw_interactions"] = interactions
            except Exception as exc:
                logger.warning("memory_inject_failed", extra={"error": str(exc)})
                context["recent_memory"] = "No prior interactions."

            business_event = await extract_event(
                raw_message=event.text or "",
                sender_id=event.sender_id,
                business_id=self.org_id,
                context=context,
                profile_repo=self.profile_repo,
                translation=translation,
            )
            # Amounts without a currency use the business's default currency
            # (Settings → Business profile), not a hard-coded UGX.
            if business_event.entities.get("amount") is not None and not business_event.entities.get("currency"):
                business_event.entities["currency"] = await self._currency()

            logger.info(
                "event_classified",
                extra={
                    "type": business_event.event_type.value,
                    "confidence": round(business_event.confidence, 2),
                    "sender": event.sender_id,
                },
            )

            # ── "That's wrong" — fix something already recorded ───────────────
            if business_event.event_type == EventType.CORRECTION:
                from services.corrections import changes_from, correction_action
                entities = business_event.entities or {}
                await self._start_correction(
                    event, correction_action(entities), entities, changes_from(entities)
                )
                return

            # ── Questions about the owner's own records ───────────────────────
            if business_event.event_type == EventType.BUSINESS_QUERY:
                await self._answer_query(event, business_event)
                await self._remember(event, "business_query", business_event.entities)
                return

            if await self._handle_simple_commands(event, business_event, context):
                return

            # ── Ask BEFORE recording when unsure about money ─────────────────
            # (Asking after dispatch recorded the transaction, and the merged
            # answer then recorded it a second time.)
            if (
                not already_clarified
                and business_event.is_financial()
                and business_event.confidence < CLARIFY_BELOW_CONFIDENCE
            ):
                from ai.reply_generator import generate_clarification
                question = await generate_clarification(
                    business_event.entities, business_event.confidence, event.text or ""
                )
                await self.pending_mgr.set_pending_clarification(event.sender_id, event.text or "", question)
                await self._reply(event, question)
                await self._remember(event, "clarification_requested", business_event.entities)
                return

            # ── Which customer is this about? ─────────────────────────────────
            from services.conversation.customer_resolution import CustomerResolver
            resolution = await asyncio.to_thread(
                CustomerResolver(self.repo, self.org_id).resolve_for_event, business_event
            )
            if resolution.needs_clarification:
                await self.pending_mgr.set_pending_clarification(
                    event.sender_id, event.text or "", resolution.clarification_question
                )
                await self._reply(event, resolution.clarification_question)
                return

            result = await self._dispatch(business_event)
            await self._after_dispatch(event, business_event, result)

        except Exception as exc:
            logger.error("event_pipeline_failed", extra={"error": str(exc), "sender": event.sender_id})
            await self._reply(event, "⚠️ Something went wrong processing your message. Please try again.")

    async def _dispatch(self, business_event, approved_actions=(), skip_actions=()):
        pipeline = getattr(self, "event_pipeline", None)
        if pipeline is not None:
            pipeline_result = await pipeline.process(business_event)
            result = pipeline_result.action_result
            if result is None:
                from domain.events.business_event import EventResult
                result = EventResult(event_id=business_event.event_id, success=True)
            return result
        dispatcher = ActionDispatcher(repo=self.repo, org_id=self.org_id, org_config=self.org_config)
        return await dispatcher.dispatch(
            business_event,
            approved_actions=approved_actions,
            skip_actions=skip_actions,
        )

    async def _after_dispatch(self, event: MessageEvent, business_event, result) -> None:
        """Reply to the owner and record what happened."""
        reply = getattr(result, "whatsapp_reply", "") or ""
        if not isinstance(reply, str):
            reply = str(reply)

        if getattr(result, "pending_approval", False) is True:
            await self.pending_mgr.set_pending_approval(
                sender_id=event.sender_id,
                event_data=business_event.to_storable(),
                action=result.pending_action or "",
                completed=list(getattr(result, "completed_actions", []) or []),
                question=reply,
            )
            await self._reply(event, reply)
            await self._remember(event, "approval_requested", business_event.entities)
            return

        if getattr(result, "needs_clarification", False) is True:
            await self.pending_mgr.set_pending_clarification(
                event.sender_id, business_event.raw_message, reply
            )
            await self._reply(event, reply)
            await self._remember(event, "clarification_requested", business_event.entities)
            return

        if not reply:
            reply = f"Recorded. {business_event.to_summary()}"
        await self._reply(event, reply)
        await self._remember(
            event,
            business_event.event_type.value if business_event.event_type else "unknown",
            business_event.entities,
        )
        await self._update_customer_memory(business_event)

        # Piggyback the overdue-reminder check on real owner activity.
        from tasks.reminder_sender import send_overdue_reminders as _send_reminders
        _spawn(
            _send_reminders(self.wa_client, self.repo, self.org_id),
            name=f"overdue_reminders:{self.org_id}",
        )

    async def _update_customer_memory(self, business_event) -> None:
        customer_id = getattr(business_event, "customer_id", None)
        if not customer_id:
            return
        payload = business_event.to_storable()
        if self.mem_repo is not None:
            try:
                from services.memory.memory_engine import update_from_event
                await update_from_event(payload, self.org_id, self.mem_repo, self.repo)
            except Exception as exc:
                logger.warning("customer_memory_update_failed", extra={"error": str(exc)})
        if self.customer_memory_svc is not None:
            try:
                await asyncio.to_thread(
                    self.customer_memory_svc.apply_customer_event,
                    self.org_id,
                    customer_id,
                    business_event.customer_name or "",
                    business_event.event_type.value,
                    business_event.entities,
                )
            except Exception as exc:
                logger.warning("customer_profile_update_failed", extra={"error": str(exc)})

    # ── Pending answers ───────────────────────────────────────────────────────

    async def _handle_approval_reply(self, event: MessageEvent, pending: dict) -> bool:
        """Owner answered a YES/NO approval question. Returns True if handled."""
        from domain.events.business_event import BusinessEvent, ProcessingStatus

        answer = _yes_no(event.text or "")
        ctx = pending.get("pending_context") or {}
        await self.pending_mgr.clear_pending(event.sender_id)

        if answer is None:
            # Not an answer — leave the paused item unconfirmed and treat this
            # as a new message.
            await self._reply(event, "ℹ️ I've left the previous item unconfirmed.")
            return False

        try:
            business_event = BusinessEvent(**(ctx.get("event") or {}))
        except Exception as exc:
            logger.error("pending_approval_event_invalid", extra={"error": str(exc)})
            await self._reply(event, "⚠️ I couldn't find the item you're confirming. Please send it again.")
            return True

        action = ctx.get("action") or ""
        completed = list(ctx.get("completed") or [])

        if answer:
            result = await self._dispatch(business_event, approved_actions={action}, skip_actions=completed)
            await self._after_dispatch(event, business_event, result)
            return True

        # NO
        if action == "update_ledger":
            business_event.processing_status = ProcessingStatus.CANCELLED
            try:
                await _repo_call(self.repo.save_business_event, self.org_id, business_event.to_storable())
            except Exception as exc:
                logger.warning("cancelled_event_persist_failed", extra={"error": str(exc)})
            await self._reply(event, "👍 Okay, I didn't record it.")
            await self._remember(event, "approval_declined", business_event.entities)
            return True

        # Declining one step (e.g. closing a promise) still lets the rest run.
        result = await self._dispatch(business_event, skip_actions=completed + [action])
        await self._after_dispatch(event, business_event, result)
        return True

    async def _handle_reminder_ack(self, event: MessageEvent, pending: dict) -> bool:
        """Owner answered a payment reminder. Returns True if handled."""
        answer = _yes_no(event.text or "")
        ctx = pending.get("pending_context") or {}
        await self.pending_mgr.clear_pending(event.sender_id)
        if answer is None:
            return False  # e.g. "Brian paid 30k" — process it as a normal message
        if answer is False:
            await self._reply(event, "👍 Okay, I'll keep it open and include it in your morning briefing.")
            return True

        from tasks.reminder_sender import mark_reminders_settled
        settled = await mark_reminders_settled(self.repo, self.org_id, ctx.get("reminder_ids") or [], event.sender_id)
        if not settled:
            await self._reply(event, "👍 Noted — I couldn't find an open reminder to close, so nothing changed.")
            return True
        lines = ["✅ *Marked as paid*"]
        for item in settled[:10]:
            amt = f" — {item.get('currency') or await self._currency()} {item['amount']:,.0f}" if item.get("amount") else ""
            lines.append(f"  • {item['debtor']}{amt}")
        await self._reply(event, "\n".join(lines))
        return True

    # ── Corrections ───────────────────────────────────────────────────────────

    def _corrections(self):
        from services.corrections import CorrectionService
        return CorrectionService(self.repo, self.org_id, dispatch=self._dispatch)

    async def _start_correction(self, event: MessageEvent, action: str, hints: dict, changes: dict) -> None:
        """Find the record the owner wants fixed and ask them to confirm."""
        from services.corrections import CHOICES_SHOWN, choice_question, describe, find_matches

        svc = self._corrections()
        try:
            records = await asyncio.to_thread(svc.recent_records)
        except Exception as exc:
            logger.error("correction_records_failed", extra={"error": str(exc)})
            await self._reply(event, "⚠️ I couldn't look up your records just now. Please try again.")
            return
        if not records:
            await self._reply(event, "I couldn't find any recent records to change.")
            return

        note = event.text or ""
        matches = find_matches(records, hints, event.sender_id)

        if action == "change" and not changes:
            last = (matches or records)[0]
            await self._reply(
                event,
                "What should it say instead? Tell me the right amount, name or date — "
                "e.g. \"it was 40k\" or \"it was Kato, not Brian\". Or say *undo* to remove it:\n"
                f"• {describe(last)}",
            )
            return

        if changes.get("customer"):
            customer_id, name, question = await asyncio.to_thread(svc.resolve_new_customer, changes["customer"])
            if question:
                await self._reply(event, question)
                return
            changes = {**changes, "customer": name, "customer_id": customer_id}

        if not matches:
            choices = records[:CHOICES_SHOWN]
            question = choice_question(choices, "I couldn't find that record. Which one do you mean?")
            await self.pending_mgr.set_pending(
                event.sender_id,
                "awaiting_correction_choice",
                {"choices": [r.to_dict() for r in choices], "action": action, "changes": changes, "note": note},
                question,
            )
            await self._reply(event, question)
            return

        await self._confirm_correction(event, action, matches[0], changes, note, other_matches=len(matches) - 1)

    async def _confirm_correction(self, event, action, target, changes, note, other_matches=0) -> None:
        from services.corrections import confirm_question, new_plan, validate_change

        if action == "change":
            problem = validate_change(target, changes)
            if problem:
                await self._reply(event, problem)
                return
        plan = new_plan(action, target, changes, note, other_matches=other_matches)
        question = confirm_question(plan)
        await self.pending_mgr.set_pending(event.sender_id, "awaiting_correction", {"plan": plan}, question)
        await self._reply(event, question)
        await self._remember(event, "correction_requested", {"action": action, "target": target.id})

    async def _handle_correction_reply(self, event: MessageEvent, pending: dict) -> bool:
        """Owner answered "fix/remove this record?". Returns True if handled."""
        answer = _yes_no(event.text or "")
        plan = (pending.get("pending_context") or {}).get("plan") or {}
        await self.pending_mgr.clear_pending(event.sender_id)
        if answer is None:
            await self._reply(event, "ℹ️ I left that record as it was.")
            return False
        if answer is False or not plan:
            await self._reply(event, "👍 Okay, I left it as it was.")
            return True

        approver = f"whatsapp:{event.sender_id.split('@')[0]}"
        try:
            reply, _affected, _new_event = await self._corrections().apply(plan, approver)
        except Exception as exc:
            logger.error("correction_apply_failed", extra={"error": str(exc)})
            await self._reply(event, "⚠️ I couldn't make that change just now. Please try again.")
            return True
        await self._reply(event, reply)
        await self._remember(event, "correction_applied", {"action": plan.get("action"), "target": (plan.get("target") or {}).get("id")})
        return True

    async def _handle_correction_choice(self, event: MessageEvent, pending: dict) -> bool:
        """Owner picked which record to fix from a numbered list."""
        from services.corrections import Record

        ctx = pending.get("pending_context") or {}
        choices = [Record.from_dict(c) for c in ctx.get("choices") or []]
        text = (event.text or "").strip()
        await self.pending_mgr.clear_pending(event.sender_id)

        answer = _yes_no(text)
        if answer is False:
            await self._reply(event, "👍 Okay, nothing changed.")
            return True
        if answer is True and choices:
            # "yes" isn't a pick — ask again for the number.
            question = f"Which one? Reply with a number from 1 to {len(choices)}, or *NO* to leave everything as it is."
            await self.pending_mgr.set_pending(event.sender_id, "awaiting_correction_choice", ctx, question)
            await self._reply(event, question)
            return True
        match =re.fullmatch(r"\s*(?:no\.?\s*|number\s*|#)?(\d{1,2})\s*[.)]?\s*", text, re.IGNORECASE)
        if not match or not (1 <= int(match.group(1)) <= len(choices)):
            await self._reply(event, "ℹ️ I didn't change anything.")
            return False
        target = choices[int(match.group(1)) - 1]
        await self._confirm_correction(
            event, ctx.get("action") or "change", target, ctx.get("changes") or {}, ctx.get("note") or ""
        )
        return True

    # ── Owner questions ───────────────────────────────────────────────────────

    async def _answer_query(self, event: MessageEvent, business_event) -> None:
        entities = business_event.entities or {}
        query_type = str(entities.get("query_type") or "").lower()
        lowered = (event.text or "").lower()
        if not query_type:
            if _mentions(lowered, ("owe", "owes", "debt", "debts", "balance", "owing")):
                query_type = "debts"
            elif _mentions(lowered, ("recent", "last", "latest")):
                query_type = "recent"
            else:
                query_type = "summary"

        if query_type in ("debts", "customer_balance"):
            name = entities.get("customer") or entities.get("debtor") or entities.get("payer")
            if not name and query_type == "customer_balance":
                name = _extract_debt_name_query(event.text or "")
            await self._send_debt_info(event, name if query_type == "customer_balance" or name else None)
        elif query_type == "recent":
            await self._send_recent_transactions(event)
        else:
            await self._send_summary(event)

    async def _handle_simple_commands(self, event: MessageEvent, business_event, context: dict) -> bool:
        """
        Greetings, help, and keyword shortcuts. Only used when the model could
        not classify the message (UNKNOWN) — e.g. "hi", or when every LLM
        provider is down and "summary" should still work.
        """
        from domain.events.event_types import EventType

        if business_event.event_type != EventType.UNKNOWN or not event.text:
            return False
        lowered = event.text.lower().strip()

        if _mentions(lowered, ("hello", "hi", "hey", "good morning", "good afternoon", "good evening")):
            await self._reply(
                event,
                f"👋 Hello {event.display_sender}! I'm SenteFlow AI.\n\n"
                "Here's what I can do:\n"
                "• ✍️ Tell me what happened: \"Brian paid 50k for feed\"\n"
                "• 📸 Send a receipt or MoMo screenshot → I'll record it\n"
                "• 🎤 Send a voice note → I'll transcribe and record it\n"
                "• 📊 Ask \"summary\" or \"who owes me?\"\n"
                "• ✏️ Fix a mistake: \"undo\" or \"Brian paid 40k not 50k\"",
            )
            return True

        if _mentions(lowered, ("summary", "summarize", "summarise")):
            await self._send_summary(event)
            return True

        if _mentions(lowered, ("recent",)) or "last transactions" in lowered:
            await self._send_recent_transactions(event)
            return True

        if _mentions(lowered, ("debt", "debts", "owes", "owe")):
            await self._send_debt_info(event, _extract_debt_name_query(event.text))
            return True

        if _mentions(lowered, ("help",)) or "what can you do" in lowered:
            await self._reply(
                event,
                "💡 *SenteFlow AI Help*\n\n"
                "*Record things:*\n"
                "• \"John paid 200k for bags\"\n"
                "• \"Sarah took 3 bags on credit, will pay Friday\"\n"
                "• \"Bought fuel 40k\"\n"
                "• Send a receipt photo or a voice note\n\n"
                "*Ask:*\n"
                "• \"summary\" — financial overview\n"
                "• \"recent\" — last 10 transactions\n"
                "• \"who owes me?\" or \"how much does Brian owe?\"\n\n"
                "*Fix a mistake:*\n"
                "• \"undo\" — remove the last thing you recorded\n"
                "• \"Brian paid 40k not 50k\"\n"
                "• \"it was Kato, not Brian\"",
            )
            return True

        return False

    # ── Receipts & documents ──────────────────────────────────────────────────

    async def _process_media_extraction(self, event: MessageEvent, source_hint: str) -> None:
        from integrations.whatsapp.media_handler import save_whatsapp_media
        from services.llm.media_processor import MediaDownloadError
        from workflows.media_extraction_workflow import run_media_extraction
        from services.responses.reply_generator import generate_extraction_reply

        if not event.media_url and not event.message_id:
            await self._reply(event, "⚠️ Couldn't access the media. Please try sending again.")
            return

        label = {MessageType.IMAGE: "📸 image", MessageType.DOCUMENT: "📄 document"}.get(event.message_type, "file")
        await self._reply(event, f"Got your {label}, {event.display_sender}! Processing...")

        try:
            file_path = await save_whatsapp_media(event, self.wa_client)
        except MediaDownloadError as exc:
            logger.error(
                "media_download_failed",
                extra={"error": str(exc), "sender": event.sender_id, "message_id": event.message_id},
            )
            await self._reply(
                event,
                "⚠️ I couldn't download that file from WhatsApp. Please try sending it again.",
            )
            return
        if not file_path:
            await self._reply(event, "⚠️ Couldn't save the media locally. Please try again.")
            return

        media_id = event.message_id
        try:
            media_id = await _repo_call(self.repo.save_media_asset, self.org_id, {
                "media_id": event.message_id,
                "sender_id": event.sender_id,
                "chat_id": event.chat_id,
                "event_id": event.event_id,
                "message_type": event.message_type.value,
                "mime_type": event.media_mime_type,
                "filename": event.media_filename,
                "storage_path": file_path,
                "source_hint": source_hint,
                "extraction_status": "processing",
                "metadata": {"timestamp": event.timestamp.isoformat(), "caption": event.text},
            })

            filename = event.media_filename or f"{source_hint}_{event.event_id}"
            # run_media_extraction is synchronous and CPU/network bound.
            result, session_id = await asyncio.to_thread(run_media_extraction, file_path, filename)

            if not result.transactions:
                await _repo_call(self.repo.save_media_asset, self.org_id, {
                    "media_id": media_id,
                    "extraction_status": "empty",
                    "extracted_entities": [],
                })
                await self._reply(
                    event,
                    f"I processed your {source_hint.replace('_', ' ')} but couldn't find any clear "
                    "transactions. Try a clearer image or describe the transaction in text.",
                )
                return

            saved_ids = await asyncio.to_thread(
                self._record_extracted_transactions, result.transactions, event, session_id
            )
            await _repo_call(self.repo.save_media_asset, self.org_id, {
                "media_id": media_id,
                "extraction_status": "completed",
                "related_transaction_ids": saved_ids,
                "extracted_entities": [txn.model_dump(mode="json") for txn in result.transactions],
                "summary": result.summary,
                "language": result.language_detected,
            })

            reply = await generate_extraction_reply(result, saved_ids)
            await self._remember(event, "receipt", {
                "transaction_ids": saved_ids,
                "confidence": result.confidence,
                "summary": result.summary,
            })
            await self._reply(event, reply)
        except Exception as exc:
            logger.error("extraction_failed", extra={"error": str(exc), "sender": event.sender_id})
            try:
                await _repo_call(self.repo.save_media_asset, self.org_id, {
                    "media_id": media_id,
                    "extraction_status": "failed",
                    "last_error": str(exc)[:500],
                })
            except Exception:
                pass
            await self._reply(event, "❌ I had trouble processing that. Please try again or send a clearer image.")

    def _record_extracted_transactions(self, transactions, event: MessageEvent, session_id: str) -> list[str]:
        """
        Save transactions read from a receipt/document. The owner sent the
        file, so the entries are approved by them; duplicates of entries
        already recorded are skipped.
        """
        from services.ledger import prepare_extracted_records

        records = []
        for txn in transactions:
            record = txn if isinstance(txn, dict) else txn.model_dump(mode="json")
            record["source"] = "whatsapp"
            records.append(record)
        records = prepare_extracted_records(self.repo, self.org_id, records, recorded_by=event.sender_id)
        approver = f"whatsapp:{event.sender_id.split('@')[0]}"
        saved_ids, _skipped = self.repo.save_approved_batch(records, self.org_id, approver, session_id)
        return saved_ids

    # ── Report helpers ────────────────────────────────────────────────────────

    async def _send_summary(self, event: MessageEvent) -> None:
        from services.responses.reply_generator import generate_summary_reply
        try:
            summary = await _repo_call(self.repo.compute_financial_summary, self.org_id)
            await self._reply(event, generate_summary_reply(summary, currency=await self._currency()))
        except Exception as exc:
            logger.error("summary_failed", extra={"error": str(exc)})
            await self._reply(event, "⚠️ Couldn't fetch summary right now. Please try again.")

    async def _send_recent_transactions(self, event: MessageEvent) -> None:
        from services.responses.reply_generator import generate_recent_reply
        try:
            transactions = await _repo_call(self.repo.list_transactions, self.org_id, limit=60)
            await self._reply(event, generate_recent_reply(transactions))
        except Exception as exc:
            logger.error("recent_transactions_failed", extra={"error": str(exc)})
            await self._reply(event, "⚠️ Couldn't fetch transactions right now.")

    async def _send_debt_info(self, event: MessageEvent, name_query: Optional[str]) -> None:
        from services.responses.reply_generator import generate_debt_reply
        from repositories.transaction_repository import SUMMARY_EVENT_LIMIT
        try:
            transactions = await _repo_call(self.repo.list_transactions, self.org_id, limit=SUMMARY_EVENT_LIMIT)
            promises = []
            lister = getattr(self.repo, "list_open_promises", None)
            if lister is not None:
                promises = await _repo_call(lister, self.org_id)
            await self._reply(
                event,
                generate_debt_reply(transactions, name_query, promises, currency=await self._currency()),
            )
        except Exception as exc:
            logger.error("debt_info_failed", extra={"error": str(exc)})
            await self._reply(event, "⚠️ Couldn't fetch debt information right now.")

    # ── Reply / memory helpers ────────────────────────────────────────────────

    async def _currency(self) -> str:
        return (await self._settings())["currency"]

    async def _settings(self) -> dict:
        from services.business_settings import business_settings
        return await business_settings(self.profile_repo, self.org_id)

    async def _normalize_language(self, event: MessageEvent) -> Optional[dict]:
        """Detect the message language and translate it to English (Sunbird AI)."""
        from services.llm.language_pipeline import normalize_business_text
        try:
            translation = await normalize_business_text(event.text or "")
        except Exception as exc:
            logger.warning("language_normalization_failed", extra={"error": str(exc)})
            return None
        self._owner_language[event.sender_id] = translation.get("language") or "eng"
        return translation

    async def _localize(self, event: MessageEvent, text: str) -> tuple[str, bool]:
        """The reply in the owner's language when the business asked for that."""
        language = self._owner_language.get(event.sender_id)
        if not text or not language or language == "eng":
            return text, False
        try:
            if (await self._settings())["reply_language"] != "same":
                return text, False
            from services.llm.language_pipeline import translate_reply
            translated = await translate_reply(text, language)
        except Exception as exc:
            logger.warning("reply_translation_failed", extra={"error": str(exc)})
            return text, False
        return (translated, True) if translated else (text, False)

    async def _reply(self, event: MessageEvent, text: str) -> None:
        # Voice-aware reply: if the owner sent a voice note, answer with one
        # (ElevenLabs), falling back to text automatically. Translated replies
        # go as text — ElevenLabs voices don't speak Luganda and the other
        # local languages well.
        text, translated = await self._localize(event, text)
        inbound_was_voice = event.message_type in (MessageType.VOICE, MessageType.AUDIO) and not translated
        try:
            from integrations.whatsapp.reply_sender import send_voice_aware
            await send_voice_aware(
                self.wa_client,
                event.chat_id,
                text,
                inbound_was_voice=inbound_was_voice,
            )
        except Exception as exc:
            logger.error("reply_failed", extra={"error": str(exc), "chat_id": event.chat_id})

    async def _remember(self, event: MessageEvent, intent: str, data: dict | None = None) -> None:
        try:
            from memory.conversation_memory import ConversationMemory
            await asyncio.to_thread(
                ConversationMemory(self.repo, self.org_id).remember_message,
                sender_id=event.sender_id,
                event_id=event.event_id,
                text=event.text,
                intent=intent or "unknown",
                extracted=data or {},
            )
        except Exception as exc:
            logger.warning("conversation_memory_write_failed", extra={"error": str(exc), "event_id": event.event_id})

    async def _reply_unsupported(self, event: MessageEvent, media_type: str) -> None:
        await self._reply(
            event,
            f"I don't support {media_type} yet. "
            "Please send receipts as photos, or use a voice note to describe transactions.",
        )


# ── Module-level helpers ──────────────────────────────────────────────────────

def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


# Words that carry no signal when the owner asks about someone's balance.
# Stripping only the command keyword left filler like "who" in the query, so
# "who owes brian" searched for the customer "who brian" and always missed.
_DEBT_QUERY_STOPWORDS = frozenset({
    "debt", "debts", "owe", "owes", "owed", "owing", "who", "whos", "who's",
    "what", "whats", "what's", "how", "much", "is", "are", "the", "my", "me",
    "show", "list", "check", "tell", "does", "do", "for", "of", "on", "to",
    "balance", "outstanding", "still", "and", "a", "an", "any", "money", "i",
    "us", "please", "all",
})


def _extract_debt_name_query(text: str) -> str | None:
    """Pull the customer name out of a free-form debt question."""
    words = re.findall(r"[\w']+", (text or "").lower())
    name_words = [w for w in words if w not in _DEBT_QUERY_STOPWORDS]
    query = " ".join(name_words).strip()
    return query or None


def _format_memory_for_prompt(interactions: list[dict]) -> str:
    """Convert recent interactions into a prompt-friendly string."""
    if not interactions:
        return "No prior interactions."
    lines = []
    for i in interactions[:5]:
        ts = str(i.get("timestamp") or "")[:16]   # trim to minute precision
        intent = i.get("intent", "?")
        msg = i.get("message") or ""
        extracted = i.get("extracted") or {}
        detail = ""
        person = extracted.get("customer") or extracted.get("payer") or extracted.get("payee")
        if person:
            amount = extracted.get("amount")
            detail = f" ({person}" + (f", {amount}" if amount else "") + ")"
        lines.append(f"- [{ts}] {intent}{detail}: {msg[:80]}")
    return "\n".join(lines)
