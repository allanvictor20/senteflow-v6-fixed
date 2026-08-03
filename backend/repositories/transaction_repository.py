"""
SenteFlow AI — TransactionRepository (Compatibility Shim)
==========================================================
This class existed in earlier versions of SenteFlow and is still referenced
by several services. It now acts as a facade over the individual repositories.

New code should use the specific repositories directly:
  - EventRepository       → organizations/{org_id}/events
  - CustomerRepository    → organizations/{org_id}/customers
  - OrderRepository       → organizations/{org_id}/orders
  - MemoryRepository      → organizations/{org_id}/memory
"""

import hashlib
import logging
import re
import uuid


from typing import Any, Optional, TYPE_CHECKING

from domain.events.event_types import EventType
from utils.clock import utc_now

if TYPE_CHECKING:
    from domain.models import FinancialSummary

logger = logging.getLogger(__name__)

_UNSAFE_DOC_ID = re.compile(r"[^A-Za-z0-9_.\-]")


def _safe_doc_id(raw: str) -> str:
    """
    Turn an arbitrary provider id into a legal Firestore document id.

    Firestore rejects ids containing "/" and caps them at 1500 bytes; WhatsApp
    message ids can contain both. Sanitising keeps the id stable (so
    de-duplication still works) instead of falling back to a random uuid.
    """
    cleaned = _UNSAFE_DOC_ID.sub("_", str(raw or "").strip())
    if not cleaned:
        return str(uuid.uuid4())
    return cleaned[:200]


def _dedupe_hash(record: dict) -> str:
    """Stable fingerprint of the fields that make a transaction unique."""
    parts = [
        str(record.get("amount") or ""),
        str(record.get("currency") or "UGX"),
        str(record.get("payer") or record.get("member_name") or ""),
        str(record.get("payee") or ""),
        str(record.get("date") or "")[:10],
        str(record.get("description") or "")[:80].strip().lower(),
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


def firestore_transactional(func):
    """
    Wrap a function with google.cloud.firestore's @transactional when the
    library is available. Falls back to calling it directly so the repository
    still imports in environments without the Firestore client installed.
    """
    try:
        from google.cloud.firestore import transactional as _transactional
        return _transactional(func)
    except Exception:  # pragma: no cover - depends on optional dependency
        return func


class TransactionRepository:
    """
    Compatibility facade used by legacy services (action_dispatcher,
    message_router, reminder_sender, api/routes.py).

    All writes go to the canonical Firestore paths used by the new
    individual repositories so data stays consistent.
    """

    def __init__(self, db):
        self._db = db

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _org(self, org_id: str):
        return self._db.collection("organizations").document(org_id)

    def _col(self, org_id: str, name: str):
        return self._org(org_id).collection(name)

    # ── Business Events (replaces transactions) ───────────────────────────────

    def save_business_event(self, org_id: str, event: dict) -> str:
        event_id = event.get("event_id") or event.get("id") or str(uuid.uuid4())
        event["event_id"] = event_id
        event.setdefault("created_at", utc_now().isoformat())
        self._col(org_id, "events").document(event_id).set(event, merge=True)
        logger.info("business_event_saved", extra={"event_id": event_id})
        return event_id

    def save_transaction(self, org_id: str, data: dict, sender_id: str = None) -> str:
        """Legacy alias — saves as a business event."""
        if sender_id:
            data["sender_id"] = sender_id
        return self.save_business_event(org_id, data)

    def get_transaction(self, org_id: str, txn_id: str) -> Optional[dict]:
        doc = self._col(org_id, "events").document(txn_id).get()
        if not doc.exists:
            return None
        return {**doc.to_dict(), "id": doc.id}

    def list_transactions(
        self,
        org_id: str,
        limit: int = 50,
        status: Optional[str] = None,
        recorded_by: Optional[str] = None,
    ) -> list[dict]:
        """
        List business events, newest first.

        `status` and `recorded_by` narrow the query server-side when supplied.
        Both are optional because most callers want the unfiltered feed.
        """
        ref = self._col(org_id, "events")
        if status:
            ref = ref.where("status", "==", status)
        if recorded_by:
            ref = ref.where("recorded_by", "==", recorded_by)
        try:
            docs = ref.order_by("created_at", direction="DESCENDING").limit(limit).get()
        except Exception as exc:
            # A filtered order_by needs a composite index. Fall back to an
            # unordered query rather than failing the whole request.
            logger.warning(
                "list_transactions_index_fallback",
                extra={"org_id": org_id, "error": str(exc)},
            )
            docs = ref.limit(limit).get()
        return [{**d.to_dict(), "id": d.id} for d in docs]

    def list_recent(self, org_id: str, limit: int = 50) -> list[dict]:
        return self.list_transactions(org_id, limit)

    def save_approved_batch(
        self,
        batch: list[dict],
        org_id: str,
        approved_by: str = "",
        session_id: str = "",
    ) -> tuple[list[str], list[dict]]:
        """
        Persist a batch of human-approved transactions.

        Returns (saved_ids, skipped) — `skipped` holds items rejected as
        duplicates of something already stored for this org, so the caller can
        report how many were dropped.
        """
        saved_ids: list[str] = []
        skipped: list[dict] = []
        seen_hashes = self._existing_dedupe_hashes(org_id)

        for item in batch:
            record = dict(item)
            fingerprint = _dedupe_hash(record)
            if fingerprint in seen_hashes:
                skipped.append(record)
                continue
            seen_hashes.add(fingerprint)
            record["status"] = "approved"
            record["approved_by"] = approved_by
            record["approved_at"] = utc_now().isoformat()
            record["upload_session_id"] = session_id or record.get("upload_session_id")
            record["dedupe_hash"] = fingerprint
            saved_ids.append(self.save_business_event(org_id, record))

        if skipped:
            logger.info(
                "approved_batch_duplicates_skipped",
                extra={"org_id": org_id, "skipped": len(skipped)},
            )
        return saved_ids, skipped

    def _existing_dedupe_hashes(self, org_id: str) -> set[str]:
        try:
            events = self.list_transactions(org_id, limit=500)
        except Exception as exc:
            logger.warning("dedupe_hash_load_failed", extra={"error": str(exc)})
            return set()
        return {e["dedupe_hash"] for e in events if e.get("dedupe_hash")}

    def compute_financial_summary(self, org_id: str) -> "FinancialSummary":
        """
        Aggregate the event log into a FinancialSummary.

        Returns a model (not a dict) because both the HTTP route and the
        WhatsApp reply builder call attribute/model methods on the result.
        """
        from domain.models import FinancialSummary

        events = self.list_transactions(org_id, limit=500)

        def _amount(e: dict) -> float:
            try:
                return float(e.get("amount") or 0)
            except (TypeError, ValueError):
                return 0.0

        def _type(e: dict) -> str:
            return e.get("event_type") or e.get("transaction_type") or e.get("type") or ""

        sale_types = {"sale", "customer_order", "order_received", "income"}
        expense_types = {"expense", "expense_recorded"}
        received_types = {"payment_received", "payment", "contribution"}

        total_sales = sum(_amount(e) for e in events if _type(e) in sale_types)
        total_expenses = sum(_amount(e) for e in events if _type(e) in expense_types)
        total_received = sum(_amount(e) for e in events if _type(e) in received_types)
        total_income = total_sales + total_received

        categories: dict[str, float] = {}
        for e in events:
            category = e.get("category") or _type(e) or "other"
            categories[category] = categories.get(category, 0.0) + _amount(e)

        pending = [e for e in events if e.get("status") == "pending"]

        return FinancialSummary(
            org_id=org_id,
            total_income=total_income,
            total_expenses=total_expenses,
            balance=total_income - total_expenses,
            total_sales=total_sales,
            total_received=total_received,
            pending_amount=sum(_amount(e) for e in pending),
            members_pending=len({e.get("payer") for e in pending if e.get("payer")}),
            members_paid=len(
                {e.get("payer") for e in events
                 if e.get("payer") and _type(e) in received_types}
            ),
            categories=categories,
        )

    def update_ai_summary(self, org_id: str, txn_id: str, summary: str) -> None:
        self._col(org_id, "events").document(txn_id).update({
            "ai_summary": summary,
            "updated_at": utc_now().isoformat(),
        })

    # ── Customers ─────────────────────────────────────────────────────────────

    def get_customer(self, org_id: str, customer_id: str) -> Optional[dict]:
        doc = self._col(org_id, "customers").document(customer_id).get()
        if not doc.exists:
            return None
        return {**doc.to_dict(), "id": doc.id}

    def get_by_phone(self, org_id: str, phone: str) -> Optional[dict]:
        docs = (
            self._col(org_id, "customers")
            .where("phone_number", "==", phone)
            .limit(1)
            .get()
        )
        for d in docs:
            return {**d.to_dict(), "id": d.id}
        return None

    def list_customers(self, org_id: str, limit: int = 100) -> list[dict]:
        docs = self._col(org_id, "customers").limit(limit).get()
        return [{**d.to_dict(), "id": d.id} for d in docs]

    def upsert_customer_profile(self, org_id: str, customer_id: str, updates: dict) -> None:
        updates["updated_at"] = utc_now().isoformat()
        self._col(org_id, "customers").document(customer_id).set(updates, merge=True)

    # ── Customer Profile ──────────────────────────────────────────────────────

    def get_customer_profile(self, org_id: str, customer_id: str) -> Optional[dict]:
        doc = self._col(org_id, "customers").document(customer_id).get()
        if not doc.exists:
            return None
        return {**doc.to_dict(), "id": doc.id}

    def get_profile(self, org_id: str, customer_id: str) -> Optional[dict]:
        return self.get_customer_profile(org_id, customer_id)

    def upsert(self, org_id: str, customer_id: str, data: dict) -> None:
        self.upsert_customer_profile(org_id, customer_id, data)

    # ── Orders ────────────────────────────────────────────────────────────────

    def create_order(self, org_id: str, order_data: dict) -> str:
        order_id = order_data.get("id") or str(uuid.uuid4())
        order_data["id"] = order_id
        order_data.setdefault("created_at", utc_now().isoformat())
        self._col(org_id, "orders").document(order_id).set(order_data, merge=True)
        return order_id

    def list_orders(
        self,
        org_id: str,
        customer_id: Optional[str] = None,
        status: Optional[str] = None,
        payment_status: Optional[str] = None,
        delivery_status: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        ref = self._col(org_id, "orders")
        for field, value in (
            ("customer_id", customer_id),
            ("status", status),
            ("payment_status", payment_status),
            ("delivery_status", delivery_status),
        ):
            if value:
                ref = ref.where(field, "==", value)
        try:
            docs = ref.order_by("created_at", direction="DESCENDING").limit(limit).get()
        except Exception as exc:
            logger.warning("list_orders_index_fallback", extra={"org_id": org_id, "error": str(exc)})
            docs = ref.limit(limit).get()
        return [{**d.to_dict(), "id": d.id} for d in docs]

    _TERMINAL_ORDER_STATUSES = frozenset({"delivered", "cancelled", "completed"})

    def list_active(self, org_id: str, limit: int = 50) -> list[dict]:
        """
        Orders that are still in flight.

        Uses a Python-side filter rather than a `not-in` query: `not-in` needs
        a composite index and silently drops documents that have no `status`
        field at all, which is exactly the set of half-written orders we most
        want to surface.
        """
        docs = self._col(org_id, "orders").limit(limit).get()
        return [
            {**d.to_dict(), "id": d.id}
            for d in docs
            if (d.to_dict().get("status") or "") not in self._TERMINAL_ORDER_STATUSES
        ]

    # ── Memory ────────────────────────────────────────────────────────────────

    def get_memory(self, org_id: str, customer_id: str) -> Optional[dict]:
        doc = self._col(org_id, "memory").document(customer_id).get()
        if not doc.exists:
            return None
        return {**doc.to_dict(), "id": doc.id}

    def update_memory(self, org_id: str, customer_id: str, data: dict) -> None:
        data["updated_at"] = utc_now().isoformat()
        self._col(org_id, "memory").document(customer_id).set(data, merge=True)

    # ── Conversations ─────────────────────────────────────────────────────────

    def get_conversation(self, org_id: str, sender_id: str) -> Optional[dict]:
        doc = self._col(org_id, "conversations").document(sender_id).get()
        if not doc.exists:
            return None
        return {**doc.to_dict(), "id": doc.id}

    def upsert_conversation(self, org_id: str, sender_id: str, data: dict) -> None:
        data["updated_at"] = utc_now().isoformat()
        self._col(org_id, "conversations").document(sender_id).set(data, merge=True)

    def append_conversation_timeline(self, org_id: str, sender_id: str, entry: dict) -> None:
        from google.cloud.firestore import ArrayUnion
        self._col(org_id, "conversations").document(sender_id).set(
            {"timeline": ArrayUnion([entry]), "updated_at": utc_now().isoformat()},
            merge=True,
        )

    def list_conversation_timeline(
        self,
        org_id: str,
        sender_id: str,
        limit: Optional[int] = None,
    ) -> list[dict]:
        """Most recent timeline entries first, newest-limited when asked."""
        data = self.get_conversation(org_id, sender_id)
        if not data:
            return []
        timeline = data.get("timeline") or []
        ordered = sorted(timeline, key=lambda e: str(e.get("timestamp") or ""), reverse=True)
        return ordered[:limit] if limit else ordered

    def get_or_create(self, org_id: str, sender_id: str, name: str = None) -> dict:
        existing = self.get_conversation(org_id, sender_id)
        if existing:
            return existing
        new_conv = {
            "sender_id": sender_id,
            "display_name": name or sender_id,
            "created_at": utc_now().isoformat(),
            "timeline": [],
        }
        self.upsert_conversation(org_id, sender_id, new_conv)
        return new_conv

    def list(self, org_id: str, limit: int = 50) -> list[dict]:
        docs = (
            self._col(org_id, "conversations")
            .order_by("updated_at", direction="DESCENDING")
            .limit(limit)
            .get()
        )
        return [{**d.to_dict(), "id": d.id} for d in docs]

    # ── Media Assets ──────────────────────────────────────────────────────────

    def save_media_asset(self, org_id: str, asset: dict) -> str:
        """
        Create or update a media asset.

        The document id is taken from "id" or "media_id" — callers doing a
        status update pass back the id returned by the create call, and both
        spellings must resolve to the same document or the update silently
        creates an orphan record instead.
        """
        asset_id = asset.get("id") or asset.get("media_id") or str(uuid.uuid4())
        asset["id"] = asset_id
        asset.setdefault("media_id", asset_id)
        asset["updated_at"] = utc_now().isoformat()
        self._col(org_id, "media_assets").document(asset_id).set(asset, merge=True)
        return asset_id

    def list_media_assets(self, org_id: str, limit: int = 50) -> list[dict]:
        docs = self._col(org_id, "media_assets").limit(limit).get()
        return [{**d.to_dict(), "id": d.id} for d in docs]

    # ── Entity Links ──────────────────────────────────────────────────────────

    def save_entity_link(self, org_id: str, link: dict) -> str:
        link_id = link.get("id") or link.get("link_id") or str(uuid.uuid4())
        link["id"] = link_id
        self._col(org_id, "entity_links").document(link_id).set(link, merge=True)
        return link_id

    def find_entity_links(
        self,
        org_id: str,
        entity_type: str,
        entity_id: str,
        limit: int = 25,
    ) -> list[dict]:
        """
        Links where the given entity appears on either side.

        Two queries because Firestore has no OR across different fields; the
        results are merged and de-duplicated by link id.
        """
        col = self._col(org_id, "entity_links")
        found: dict[str, dict] = {}
        for type_field, id_field in (
            ("source_entity_type", "source_entity_id"),
            ("target_entity_type", "target_entity_id"),
        ):
            try:
                docs = (
                    col.where(type_field, "==", entity_type)
                    .where(id_field, "==", entity_id)
                    .limit(limit)
                    .get()
                )
            except Exception as exc:
                logger.warning(
                    "entity_link_query_failed",
                    extra={"field": id_field, "error": str(exc)},
                )
                continue
            for d in docs:
                found[d.id] = {**d.to_dict(), "id": d.id}
        return list(found.values())[:limit]

    # ── Webhook Queue ─────────────────────────────────────────────────────────

    def enqueue_webhook_event(
        self,
        org_id: str,
        event_id: str,
        payload: dict,
        normalized_event: dict,
    ) -> tuple[bool, str]:
        """
        Queue a webhook event for background processing, keyed by the
        provider's event id so retries do not process the same message twice.

        Returns (created, queue_id). `created` is False when this event_id was
        already queued — the caller should then respond "duplicate" and do
        nothing else.
        """
        queue_id = _safe_doc_id(event_id)
        doc_ref = self._col(org_id, "webhook_queue").document(queue_id)

        @firestore_transactional
        def _claim(transaction, ref):
            snapshot = ref.get(transaction=transaction)
            if snapshot.exists:
                return False
            transaction.set(ref, {
                "id": queue_id,
                "event_id": event_id,
                "payload": payload,
                "normalized_event": normalized_event,
                "status": "queued",
                "attempts": 0,
                "created_at": utc_now().isoformat(),
            })
            return True

        try:
            return _claim(self._db.transaction(), doc_ref), queue_id
        except Exception as exc:
            # Without transaction support (e.g. a stub db in tests) fall back to
            # a read-then-write. Slightly racier, but still de-duplicates.
            logger.warning("webhook_enqueue_non_transactional", extra={"error": str(exc)})
            if doc_ref.get().exists:
                return False, queue_id
            doc_ref.set({
                "id": queue_id,
                "event_id": event_id,
                "payload": payload,
                "normalized_event": normalized_event,
                "status": "queued",
                "attempts": 0,
                "created_at": utc_now().isoformat(),
            })
            return True, queue_id

    def get_queued_webhook_event(self, org_id: str, queue_id: Optional[str] = None) -> Optional[dict]:
        """Fetch a specific queued event by id, or the oldest one if omitted."""
        if queue_id:
            doc = self._col(org_id, "webhook_queue").document(queue_id).get()
            if not doc.exists:
                return None
            return {**doc.to_dict(), "id": doc.id}

        docs = (
            self._col(org_id, "webhook_queue")
            .where("status", "==", "queued")
            .order_by("created_at")
            .limit(1)
            .get()
        )
        for d in docs:
            return {**d.to_dict(), "id": d.id}
        return None

    def mark_webhook_processing(self, org_id: str, queue_id: str) -> bool:
        """
        Claim a queued event for processing.

        Returns True only if this caller won the claim. Returns False when the
        event is already being processed or is finished, so a duplicate
        delivery cannot run the pipeline a second time.
        """
        doc_ref = self._col(org_id, "webhook_queue").document(queue_id)
        try:
            doc = doc_ref.get()
            if not doc.exists:
                logger.warning("webhook_claim_missing", extra={"queue_id": queue_id})
                return False
            if doc.to_dict().get("status") != "queued":
                return False
            doc_ref.update({
                "status": "processing",
                "attempts": (doc.to_dict().get("attempts") or 0) + 1,
                "started_at": utc_now().isoformat(),
            })
            return True
        except Exception as exc:
            logger.error("webhook_claim_failed", extra={"queue_id": queue_id, "error": str(exc)})
            return False

    def mark_webhook_completed(self, org_id: str, queue_id: str) -> None:
        self._col(org_id, "webhook_queue").document(queue_id).update({
            "status": "completed",
            "completed_at": utc_now().isoformat(),
        })

    def mark_webhook_failed(self, org_id: str, queue_id: str, error: str = "") -> None:
        self._col(org_id, "webhook_queue").document(queue_id).update({
            "status": "failed",
            "error": error,
            "failed_at": utc_now().isoformat(),
        })

    # ── Debts / Overdue ───────────────────────────────────────────────────────

    def list_overdue(self, org_id: str, limit: int = 50) -> list[dict]:
        """
        Unresolved payment promises and debts.

        Filters in Python on the "resolved" flag rather than in the query:
        events written by the dispatcher do not set that field at all, and a
        Firestore equality filter skips documents where the field is absent —
        which would make every promise invisible here.
        """
        docs = (
            self._col(org_id, "events")
            .where("event_type", "in", [
                EventType.PAYMENT_PROMISE.value,
                EventType.DEBT_CREATED.value,
            ])
            .limit(limit)
            .get()
        )
        events = [{**d.to_dict(), "id": d.id} for d in docs]
        return [
            e for e in events
            if not e.get("resolved") and e.get("status") not in ("resolved", "cancelled")
        ]

    def list_by_customer(self, org_id: str, customer_id: str, limit: int = 50) -> list[dict]:
        docs = (
            self._col(org_id, "events")
            .where("sender_id", "==", customer_id)
            .order_by("created_at", direction="DESCENDING")
            .limit(limit)
            .get()
        )
        return [{**d.to_dict(), "id": d.id} for d in docs]

    def list_requiring_followup(self, org_id: str, limit: int = 50) -> list[dict]:
        return self.list_overdue(org_id, limit)

    def complete_task(self, org_id: str, task_id: str) -> None:
        self._col(org_id, "tasks").document(task_id).update({
            "status": "completed",
            "completed_at": utc_now().isoformat(),
        })
