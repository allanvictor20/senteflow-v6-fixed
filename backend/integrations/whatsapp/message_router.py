"""
SenteFlow AI — Message Router (v6 — Unified Event Pipeline)
============================================================
Gap 2 fix: Removed dual routing. Everything goes through _route_via_event_pipeline.
Gap 5 fix: Added PendingStateManager for multi-turn clarification state.

BUG FIX (v6.1): MessageEvent.model_copy() replaces the broken __dict__ splat.
  Old (crashes):  MessageEvent(**{**event.__dict__, "text": merged_text})
  New (correct):  event.model_copy(update={"text": merged_text})

Old flow (v5, broken):
  text → classify_intent → if whitelisted → run_message_workflow (legacy)
                         → else          → detect_intent (second LLM call)
                         → else          → _route_via_event_pipeline

New flow (v6, unified):
  text → check pending_state → if awaiting_clarification → merge + re-run
       → _route_via_event_pipeline (ContextEngine → EventInterpreter → ActionDispatcher)

Summary / help / greeting shortcuts are handled inside _handle_simple_commands
so we don't need pre-routing for those.
"""

import logging
import inspect
from typing import TYPE_CHECKING

from core.message_event import MessageEvent, MessageType
from services.actions.action_dispatcher import ActionDispatcher
from services.llm.event_extractor import extract_event

if TYPE_CHECKING:
    from integrations.whatsapp.client import EvolutionClient
    from repositories.transaction_repository import TransactionRepository

logger = logging.getLogger(__name__)


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


# ── Gap 5: Pending Clarification State ────────────────────────────────────────

class PendingStateManager:
    """
    Tracks whether the bot has asked a clarification question and is
    waiting for the user to answer it.  State lives in Firestore via repo.
    """

    def __init__(self, repo, org_id: str):
        self.repo = repo
        self.org_id = org_id
        self._pending_cache: dict[str, dict] = {}

    async def set_pending_clarification(
        self,
        sender_id: str,
        original_text: str,
        clarification_question: str,
    ) -> None:
        try:
            await _maybe_await(self.repo.upsert_conversation(
                self.org_id,
                sender_id,
                {
                    "pending_intent": "awaiting_clarification",
                    "pending_context": {"original_text": original_text},
                    "pending_question": clarification_question,
                },
            ))
            self._pending_cache[sender_id] = {
                "pending_intent": "awaiting_clarification",
                "pending_context": {"original_text": original_text},
                "pending_question": clarification_question,
            }
        except Exception as exc:
            logger.warning("pending_state_write_failed", extra={"error": str(exc)})

    async def get_pending_state(self, sender_id: str) -> dict | None:
        try:
            conv = await _maybe_await(self.repo.get_conversation(self.org_id, sender_id))
            if conv and conv.get("pending_intent"):
                return conv
            if sender_id in self._pending_cache:
                return self._pending_cache[sender_id]
        except Exception as exc:
            logger.warning("pending_state_read_failed", extra={"error": str(exc)})
        return None

    async def clear_pending(self, sender_id: str) -> None:
        try:
            await _maybe_await(self.repo.upsert_conversation(
                self.org_id,
                sender_id,
                {"pending_intent": None, "pending_context": None, "pending_question": None},
            ))
            self._pending_cache.pop(sender_id, None)
        except Exception as exc:
            logger.warning("pending_state_clear_failed", extra={"error": str(exc)})


# ── Router ─────────────────────────────────────────────────────────────────────

class MessageRouter:
    """Routes normalized MessageEvent objects to the unified event pipeline."""

    def __init__(
        self,
        wa_client: "EvolutionClient",
        repo: "TransactionRepository",
        org_id: str = "default",
        mem_repo=None,
        profile_repo=None,
        org_config=None,
    ):
        self.wa_client = wa_client
        self.repo = repo
        self.org_id = org_id
        self.mem_repo = mem_repo
        self.profile_repo = profile_repo
        self.org_config = org_config
        # Set by main.py after all bounded contexts are initialised
        self.event_pipeline = None

    async def route(self, event: MessageEvent) -> None:
        logger.info(
            "routing_message",
            extra={"type": event.message_type.value, "sender": event.display_sender},
        )

        match event.message_type:
            case MessageType.TEXT:
                await self._route_text(event)
            case MessageType.IMAGE:
                await self._route_via_event_pipeline(event)
            case MessageType.VOICE | MessageType.AUDIO:
                await self._route_via_event_pipeline(event)
            case MessageType.DOCUMENT:
                await self._route_via_event_pipeline(event)
            case MessageType.VIDEO:
                await self._reply_unsupported(event, "video files")
            case MessageType.STICKER:
                pass
            case MessageType.LOCATION:
                await self._reply(event, "📍 Location noted. I can only process financial records for now.")
            case _:
                await self._reply(
                    event,
                    "I received your message but couldn't understand the format. "
                    "Try sending a receipt photo, voice note, or text like \"Brian paid 50k for feed\".",
                )

    # ── Gap 2: Unified text routing ───────────────────────────────────────────

    async def _route_text(self, event: MessageEvent) -> None:
        """
        Gap 2 fix: All text messages flow through a single unified path.
        Gap 5 fix: Check for pending clarification state first.
        BUG FIX:   Use model_copy() instead of **__dict__ splat to rebuild MessageEvent.
        """
        if not event.text:
            return

        pending_mgr = PendingStateManager(self.repo, self.org_id)

        # Gap 5 — check if bot is waiting for an answer to a clarification question
        pending = await pending_mgr.get_pending_state(event.sender_id)
        if pending and pending.get("pending_intent") == "awaiting_clarification":
            original_text = pending.get("pending_context", {}).get("original_text", "")
            # Merge the original ambiguous message with the user's clarification reply
            merged_text = f"{original_text} — {event.text or ''}"
            logger.info(
                "resolving_pending_clarification",
                extra={"sender": event.sender_id, "merged": merged_text[:80]},
            )
            await pending_mgr.clear_pending(event.sender_id)

            # BUG FIX: use model_copy() — the only correct way to copy a Pydantic model
            # with field overrides.  **__dict__ splat breaks because Pydantic stores
            # internal state (__fields_set__, __private_attributes__) alongside field
            # values; splatting all of them into the constructor raises TypeError.
            merged_event = event.model_copy(update={"text": merged_text})
            await self._route_via_event_pipeline(merged_event)
            return

        # Gap 2 — all text flows through the unified event pipeline
        await self._route_via_event_pipeline(event)

    # ── Unified Event Pipeline ─────────────────────────────────────────────────

    async def _route_via_event_pipeline(self, event: MessageEvent) -> None:
        """
        Unified path:
          ContextEngine → EventInterpreter → ActionDispatcher → WhatsApp reply

        Gap 3: ContextEngine is called for ALL messages (text and media).
        Gap 4: Memory is fetched and injected into context before AI calls.
        Gap 5: Clarification replies set pending state in Firestore.
        """
        from services.memory.context_engine import ContextEngine
        from services.llm.event_extractor import extract_event as EventInterpreter
        # Acknowledge media up front so the user knows we got it
        if event.is_media:
            media_label = {
                MessageType.IMAGE: "📸 image",
                MessageType.VOICE: "🎤 voice note",
                MessageType.AUDIO: "🎤 voice note",
                MessageType.DOCUMENT: "📄 document",
            }.get(event.message_type, "file")
            await self._reply(event, f"Got your {media_label}, {event.display_sender}! Processing...")

        try:
            # ── Gap 3: Build context for EVERY message before any AI call ────
            context_engine = ContextEngine(repo=self.repo, org_id=self.org_id, mem_repo=self.mem_repo)
            context = await context_engine.get_context(
                sender_id=event.sender_id,
                raw_message=event.text or "",
            )
            logger.info(
                "context_built",
                extra={
                    "sender": event.sender_id,
                    "customer_risk": context.get("customer", {}).get("risk_level", "unknown"),
                    "open_orders": len(context.get("open_orders", [])),
                },
            )

            # ── Gap 4: Inject conversation memory into context ────────────────
            try:
                from memory.conversation_memory import ConversationMemory
                interactions = ConversationMemory(self.repo, self.org_id).last_interactions(
                    event.sender_id, limit=5
                )
                context["recent_memory"] = _format_memory_for_prompt(interactions)
                context["raw_interactions"] = interactions
            except Exception as exc:
                logger.warning("memory_inject_failed", extra={"error": str(exc)})
                context["recent_memory"] = "No prior interactions."
            context["wa_client"] = self.wa_client

            # ── Interpret → BusinessEvent ─────────────────────────────────────
            interpreter = EventInterpreter(business_id=self.org_id)
            business_event = await interpreter.interpret(event, context)

            logger.info(
                "event_classified",
                extra={
                    "type": business_event.event_type.value,
                    "confidence": round(business_event.confidence, 2),
                    "sender": event.sender_id,
                },
            )

            # ── Media: fall through to legacy extraction ──────────────────────
            if business_event.recommended_actions == ["run_extraction_workflow"]:
                await self._process_media_extraction(event, source_hint=event.message_type.value)
                return

            # ── Handle simple commands that don't need ActionDispatcher ───────
            command_handled = await self._handle_simple_commands(event, business_event, context)
            if command_handled:
                return

            # ── Dispatch actions through EventPipeline (new) or ActionDispatcher ──
            pipeline = getattr(self, "event_pipeline", None)
            if pipeline is not None:
                pipeline_result = await pipeline.process(business_event)
                result = pipeline_result.action_result
                if result is None:
                    # Fallback: create a minimal result so reply logic works
                    from domain.events.business_event import EventResult
                    result = EventResult(event_id=business_event.event_id, success=True)
            else:
                dispatcher = ActionDispatcher(repo=self.repo, org_id=self.org_id, org_config=self.org_config)
                result = await dispatcher.dispatch(business_event)

            raw_reply = result.whatsapp_reply
            is_clarification = False

            if business_event.is_financial() and business_event.entities.get("amount"):
                from ai.reply_generator import generate_smart_confirmation

                reply_obj = await generate_smart_confirmation(
                    data=business_event.entities,
                    confidence=business_event.confidence,
                    original_text=event.text or "",
                )
                raw_reply = reply_obj.text
                is_clarification = reply_obj.is_clarification
            elif not raw_reply:
                raw_reply = f"Recorded. {business_event.to_summary()}"

            if is_clarification:
                await PendingStateManager(self.repo, self.org_id).set_pending_clarification(
                    sender_id=event.sender_id,
                    original_text=event.text or "",
                    clarification_question=raw_reply,
                )

            await self._reply(event, raw_reply)
            await self._remember(
                event,
                business_event.event_type.value if business_event.event_type else "unknown",
                business_event.entities,
            )

            if business_event.requires_followup():
                from services.memory.memory_engine import BusinessMemoryEngine
                memory = BusinessMemoryEngine(repo=self.repo, org_id=self.org_id)
                memory.remember_event(business_event)

            # FIX (v2): Piggyback reminder check on every real message — no scheduler needed.
            # Fires overdue reminders when the business owner is actively texting the bot.
            # Runs as a non-blocking task so it never delays the current reply.
            import asyncio as _asyncio
            from tasks.reminder_sender import send_overdue_reminders as _send_reminders
            _asyncio.create_task(
                _send_reminders(self.wa_client, self.repo, self.org_id)
            )

        except Exception as exc:
            logger.error("event_pipeline_failed", extra={"error": str(exc), "sender": event.sender_id})
            await self._reply(event, "⚠️ Something went wrong processing your message. Please try again.")

    async def _handle_simple_commands(self, event: MessageEvent, business_event, context: dict) -> bool:
        """
        Handle greetings, summaries, help, and debt queries that don't need
        ActionDispatcher.  Returns True if handled, False to continue dispatch.
        """
        from domain.events.event_types import EventType

        if business_event.event_type == EventType.UNKNOWN and event.text:
            lowered = event.text.lower().strip()

            if any(term in lowered for term in ("hello", "hi", "hey", "good morning", "good afternoon")):
                await self._reply(
                    event,
                    f"👋 Hello {event.display_sender}! I'm SenteFlow AI.\n\n"
                    "Here's what I can do:\n"
                    "• 📸 Send a receipt photo → I'll extract the transaction\n"
                    "• 🎤 Send a voice note → I'll transcribe and record it\n"
                    "• ✍️ Text me: \"Brian paid 50k for feed\" → I'll record it\n"
                    "• 📊 Text \"summary\" → I'll send your financial overview\n"
                    "• 📋 Text \"recent\" → I'll list recent transactions",
                )
                return True

            if any(term in lowered for term in ("summary", "summarize")):
                await self._send_summary(event)
                return True

            if any(term in lowered for term in ("recent", "last transactions")):
                await self._send_recent_transactions(event)
                return True

            if "debt" in lowered or "who owes" in lowered:
                await self._send_debt_info(event, event.text)
                return True

            if any(term in lowered for term in ("help", "what can you do")):
                await self._reply(
                    event,
                    "💡 *SenteFlow AI Help*\n\n"
                    "*Record a transaction:*\n"
                    "• Send a receipt photo\n"
                    "• Send a voice note describing the transaction\n"
                    "• Type: \"John paid 200k for bags\"\n\n"
                    "*Get reports:*\n"
                    "• Type \"summary\" for financial overview\n"
                    "• Type \"recent\" for last 10 transactions\n"
                    "• Type \"debt [name]\" to check someone's balance",
                )
                return True

        return False

    # ── Legacy Media Extraction ───────────────────────────────────────────────

    async def _process_media_extraction(self, event: MessageEvent, source_hint: str) -> None:
        from integrations.whatsapp.media_handler import save_whatsapp_media
        from services.llm.media_processor import MediaDownloadError
        from workflows.event_extraction_workflow import run_event_extraction as run_extraction
        from services.responses.reply_generator import generate_extraction_reply
        from services.actions.action_dispatcher import ActionDispatcher as _AD
        async def record_transaction_batch(transactions, org_id, sender_id, session_id, repo):
            ids = []
            for txn in transactions:
                event_id = repo.save_business_event(org_id, txn if isinstance(txn, dict) else txn.model_dump(mode="json"))
                ids.append(event_id)
            return ids

        if not event.media_url:
            await self._reply(event, "⚠️ Couldn't access the media. Please try sending again.")
            return

        # FIX (v2): catch MediaDownloadError explicitly for a user-friendly message
        try:
            file_path = await save_whatsapp_media(event, self.wa_client)
        except MediaDownloadError as exc:
            logger.error(
                "media_download_failed",
                extra={"error": str(exc), "sender": event.sender_id, "url": str(event.media_url)[:80]},
            )
            await self._reply(
                event,
                "⚠️ I couldn't download that file from WhatsApp. "
                "This sometimes happens when the file takes too long to reach me. "
                "Please try sending it again.",
            )
            return
        if not file_path:
            await self._reply(event, "⚠️ Couldn't save the media locally. Please try again.")
            return

        media_id = self.repo.save_media_asset(self.org_id, {
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

        try:
            filename = event.media_filename or f"{source_hint}_{event.event_id}"
            import asyncio as _asyncio
            result, session_id = await _asyncio.to_thread(run_extraction, file_path, filename)
        except Exception as exc:
            logger.error("extraction_failed", extra={"error": str(exc), "sender": event.sender_id})
            self.repo.save_media_asset(self.org_id, {
                "media_id": media_id,
                "extraction_status": "failed",
                "last_error": str(exc),
            })
            await self._reply(event, "❌ I had trouble processing that. Please try again or send a clearer image.")
            return

        if not result.transactions:
            self.repo.save_media_asset(self.org_id, {
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

        saved_ids = await record_transaction_batch(
            result.transactions, self.org_id, event.sender_id, session_id, self.repo
        )
        self.repo.save_media_asset(self.org_id, {
            "media_id": media_id,
            "extraction_status": "completed",
            "related_transaction_ids": saved_ids,
            "extracted_entities": [txn.model_dump(mode="json") for txn in result.transactions],
            "summary": result.summary,
            "language": result.language_detected,
        })

        try:
            from services.conversation import ConversationStateManager, EntityLinker
            conversation = await _maybe_await(ConversationStateManager(self.repo, self.org_id).record_media_processed(
                sender_id=event.sender_id,
                event_id=event.event_id,
                media_id=media_id,
                transaction_ids=saved_ids,
                summary=result.summary,
            ))
            await _maybe_await(EntityLinker(self.repo, self.org_id).link_media_extraction(
                sender_id=event.sender_id,
                event_id=event.event_id,
                media_id=media_id,
                transaction_ids=saved_ids,
                active_order_id=conversation.get("active_order_id"),
            ))
        except Exception as exc:
            logger.warning("media_relationship_update_failed", extra={"error": str(exc), "media_id": media_id})

        reply = await generate_extraction_reply(result, saved_ids)
        await self._remember(event, "receipt", {
            "transaction_ids": saved_ids,
            "confidence": result.confidence,
            "summary": result.summary,
        })
        await self._reply(event, reply)

    # ── Report Helpers ────────────────────────────────────────────────────────

    async def _send_summary(self, event: MessageEvent) -> None:
        from services.responses.reply_generator import generate_summary_reply
        try:
            summary = await self.repo.compute_financial_summary(self.org_id)
            await self._reply(event, generate_summary_reply(summary))
        except Exception as exc:
            logger.error("summary_failed", extra={"error": str(exc)})
            await self._reply(event, "⚠️ Couldn't fetch summary right now. Please try again.")

    async def _send_recent_transactions(self, event: MessageEvent) -> None:
        from services.responses.reply_generator import generate_recent_reply
        try:
            transactions = await self.repo.list_transactions(self.org_id, limit=10)
            await self._reply(event, generate_recent_reply(transactions))
        except Exception as exc:
            logger.error("recent_transactions_failed", extra={"error": str(exc)})
            await self._reply(event, "⚠️ Couldn't fetch transactions right now.")

    async def _send_debt_info(self, event: MessageEvent, text: str) -> None:
        from services.responses.reply_generator import generate_debt_reply
        words = text.lower().replace("debt", "").replace("owes", "").strip().split()
        name_query = " ".join(words).strip() if words else None
        try:
            transactions = await self.repo.list_transactions(self.org_id)
            await self._reply(event, generate_debt_reply(transactions, name_query))
        except Exception as exc:
            logger.error("debt_info_failed", extra={"error": str(exc)})
            await self._reply(event, "⚠️ Couldn't fetch debt information right now.")

    # ── Reply / Memory Helpers ────────────────────────────────────────────────

    async def _reply(self, event: MessageEvent, text: str) -> None:
        try:
            from integrations.whatsapp.reply_sender import send
            await send(self.wa_client, event.chat_id, text)
        except Exception as exc:
            logger.error("reply_failed", extra={"error": str(exc), "chat_id": event.chat_id})

    async def _remember(self, event: MessageEvent, intent: str, data: dict | None = None) -> None:
        try:
            from memory.conversation_memory import ConversationMemory
            ConversationMemory(self.repo, self.org_id).remember_message(
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

def _format_memory_for_prompt(interactions: list[dict]) -> str:
    """Gap 4: Convert recent interactions into a prompt-friendly string."""
    if not interactions:
        return "No prior interactions."
    lines = []
    for i in interactions[:5]:
        ts = i.get("timestamp", "")[:16]   # trim to minute precision
        intent = i.get("intent", "?")
        msg = i.get("message") or ""
        extracted = i.get("extracted") or {}
        detail = ""
        if extracted.get("payer") or extracted.get("payee"):
            person = extracted.get("payer") or extracted.get("payee")
            amount = extracted.get("amount")
            detail = f" ({person}" + (f", {amount}" if amount else "") + ")"
        lines.append(f"- [{ts}] {intent}{detail}: {msg[:80]}")
    return "\n".join(lines)