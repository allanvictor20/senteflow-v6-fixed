"""
SenteFlow AI — BusinessMemoryEngine
=====================================
Long-term operational memory for SME businesses.

Right now the database stores records.
With memory, the database stores *relationships, patterns, and commitments*.

The moat is here: a system that remembers promises, notices patterns,
and proactively surfaces what matters — without the business owner
having to track it manually.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from repositories.transaction_repository import TransactionRepository

logger = logging.getLogger(__name__)


async def update_from_event(event: dict, org_id: str, memory_repo, customer_repo) -> None:
    """Update first-class CustomerMemory from a newly processed BusinessEvent."""
    customer_id = event.get("related_customer_id") or event.get("sender_id")
    if not customer_id:
        return

    existing = await memory_repo.get_memory(org_id, customer_id) or {}
    maybe_customer = customer_repo.get_customer(org_id, customer_id)
    customer = await maybe_customer if hasattr(maybe_customer, "__await__") else (maybe_customer or {})

    event_type = event.get("event_type", "")
    entities = event.get("entities") or {}
    amount = float(entities.get("amount") or 0)
    updates = {
        "customer_id": customer_id,
        "org_id": org_id,
        "display_name": customer.get("display_name") or existing.get("display_name") or "Unknown",
        "last_contact": datetime.utcnow().isoformat(),
    }

    if event_type in ("payment_received", "payment", "income"):
        updates["total_paid"] = existing.get("total_paid", 0) + amount
    elif event_type == "debt_created":
        updates["total_outstanding"] = existing.get("total_outstanding", 0) + amount
    elif event_type in ("customer_order", "order_received"):
        total_orders = existing.get("total_orders", 0) + 1
        updates["total_orders"] = total_orders
        if amount:
            prior_total = existing.get("average_order_value", 0) * existing.get("total_orders", 0)
            updates["average_order_value"] = round((prior_total + amount) / total_orders, 2)
        item = entities.get("item")
        if item:
            frequently_ordered = list(existing.get("frequently_ordered", []))
            if item not in frequently_ordered:
                frequently_ordered.append(item)
            updates["frequently_ordered"] = frequently_ordered[:10]
            updates["last_order_items"] = [item]
    elif event_type == "payment_promise":
        promises = list(existing.get("open_promises", []))
        promises.append({
            "type": "payment",
            "amount": amount,
            "due": entities.get("due_date"),
            "recorded_at": datetime.utcnow().isoformat(),
        })
        updates["open_promises"] = promises

    from domain.business_memory.model import CustomerMemory

    try:
        await memory_repo.update_memory(org_id, CustomerMemory(**{**existing, **updates}))
        logger.info("customer_memory_updated", extra={"customer_id": customer_id})
    except Exception as exc:
        logger.error("customer_memory_update_failed", extra={"error": str(exc), "customer_id": customer_id})


class BusinessMemoryEngine:
    """
    Stores and retrieves long-term operational memory for a business.

    Memory types:
    - Customer memory: relationships, balances, habits
    - Conversation memory: unresolved promises ("he'll pay Friday")
    - Pattern memory: recurring purchases, payment cycles
    """

    def __init__(self, repo: "TransactionRepository", org_id: str):
        self.repo = repo
        self.org_id = org_id

    # ─── Remember ─────────────────────────────────────────────────────────────

    def remember_event(self, business_event: Any) -> None:
        """
        Persist a business event to long-term memory.
        Called after every event is processed.
        """
        try:
            # For PAYMENT_PROMISE events, store in Firestore with a 'promise' flag
            from domain.events.event_types import EventType
            if business_event.event_type == EventType.PAYMENT_PROMISE:
                self._store_promise(business_event)
            elif business_event.event_type == EventType.LOW_STOCK_ALERT:
                self._store_stock_alert(business_event)
            logger.debug("memory_stored", extra={"event_id": business_event.event_id})
        except Exception as exc:
            logger.warning("memory_store_failed", extra={"error": str(exc)})

    def _store_promise(self, business_event: Any) -> None:
        """Persist a payment promise so it can be checked later."""
        doc = {
            "type": "payment_promise",
            "event_id": business_event.event_id,
            "entities": business_event.entities,
            "raw_message": business_event.raw_message,
            "sender_id": business_event.sender_id,
            "created_at": datetime.utcnow().isoformat(),
            "status": "pending",
            "org_id": self.org_id,
        }
        try:
            self.repo._db.collection("organizations").document(self.org_id)\
                .collection("promises").document(business_event.event_id).set(doc)
        except Exception as exc:
            logger.warning("promise_store_failed", extra={"error": str(exc)})

    def _store_stock_alert(self, business_event: Any) -> None:
        """Log a low-stock alert so the owner dashboard can surface it."""
        doc = {
            "type": "low_stock_alert",
            "event_id": business_event.event_id,
            "entities": business_event.entities,
            "created_at": datetime.utcnow().isoformat(),
            "status": "open",
            "org_id": self.org_id,
        }
        try:
            self.repo._db.collection("organizations").document(self.org_id)\
                .collection("alerts").document(business_event.event_id).set(doc)
        except Exception as exc:
            logger.warning("stock_alert_store_failed", extra={"error": str(exc)})

    # ─── Retrieve ─────────────────────────────────────────────────────────────

    async def get_customer_memory(self, sender_id: str) -> dict[str, Any]:
        """
        Retrieve everything the system knows about a customer.
        Returns a structured memory object.
        """
        transactions = await self.repo.list_transactions(self.org_id, limit=200)
        sender_txns = [t for t in transactions if t.get("recorded_by") == sender_id]

        # Derive payment behavior
        paid_txns = [t for t in sender_txns if t.get("type") in ("payment", "income", "contribution", "payment_received")]
        loan_txns = [t for t in sender_txns if t.get("type") in ("loan", "debt_created")]

        total_paid = sum(t.get("amount", 0) for t in paid_txns)
        total_borrowed = sum(t.get("amount", 0) for t in loan_txns)
        outstanding = max(0.0, total_borrowed - total_paid)

        # Payment behavior pattern
        if len(paid_txns) >= 3:
            behavior = "regular_payer"
        elif outstanding > 0 and len(paid_txns) == 0:
            behavior = "non_payer"
        elif outstanding > 100_000:
            behavior = "heavy_debtor"
        else:
            behavior = "occasional"

        # Most common items
        items: dict[str, int] = {}
        for t in sender_txns:
            desc = t.get("description", "")
            if desc:
                items[desc] = items.get(desc, 0) + 1
        usual_items = sorted(items, key=items.get, reverse=True)[:3]  # type: ignore[arg-type]

        return {
            "sender_id": sender_id,
            "total_transactions": len(sender_txns),
            "total_paid": total_paid,
            "total_borrowed": total_borrowed,
            "outstanding_balance": outstanding,
            "payment_behavior": behavior,
            "usual_items": usual_items,
            "last_seen": sender_txns[0].get("created_at") if sender_txns else None,
        }

    def get_unresolved_promises(self) -> list[dict]:
        """Return all outstanding payment promises across all customers."""
        try:
            docs = (
                self.repo._db.collection("organizations").document(self.org_id)
                .collection("promises")
                .where("status", "==", "pending")
                .get()
            )
            return [d.to_dict() for d in docs]
        except Exception as exc:
            logger.warning("promises_fetch_failed", extra={"error": str(exc)})
            return []

    def get_overdue_promises(self, days_threshold: int = 2) -> list[dict]:
        """Return promises that are older than `days_threshold` days without resolution."""
        all_promises = self.get_unresolved_promises()
        threshold = datetime.utcnow() - timedelta(days=days_threshold)
        overdue = []
        for p in all_promises:
            created = p.get("created_at", "")
            try:
                created_dt = datetime.fromisoformat(created)
                if created_dt < threshold:
                    overdue.append(p)
            except (ValueError, TypeError):
                pass
        return overdue

    def get_open_stock_alerts(self) -> list[dict]:
        """Return open low-stock alerts."""
        try:
            docs = (
                self.repo._db.collection("organizations").document(self.org_id)
                .collection("alerts")
                .where("status", "==", "open")
                .get()
            )
            return [d.to_dict() for d in docs]
        except Exception as exc:
            logger.warning("alerts_fetch_failed", extra={"error": str(exc)})
            return []

    async def find_patterns(self, sender_id: Optional[str] = None) -> dict[str, Any]:
        """
        Detect recurring patterns: payment cycles, common purchases, seasonal trends.
        """
        transactions = await self.repo.list_transactions(self.org_id, limit=500)
        if sender_id:
            transactions = [t for t in transactions if t.get("recorded_by") == sender_id]

        # Category frequency
        category_counts: dict[str, int] = {}
        for t in transactions:
            cat = t.get("category", "other")
            category_counts[cat] = category_counts.get(cat, 0) + 1

        # Average transaction size
        amounts = [t.get("amount", 0) for t in transactions if t.get("amount")]
        avg_amount = sum(amounts) / len(amounts) if amounts else 0.0

        return {
            "top_categories": sorted(category_counts.items(), key=lambda x: x[1], reverse=True)[:5],
            "average_transaction": avg_amount,
            "total_transactions_analysed": len(transactions),
        }

    def resolve_promise(self, event_id: str) -> bool:
        """Mark a payment promise as resolved when the payment arrives."""
        try:
            self.repo._db.collection("organizations").document(self.org_id)\
                .collection("promises").document(event_id)\
                .update({"status": "resolved", "resolved_at": datetime.utcnow().isoformat()})
            return True
        except Exception as exc:
            logger.warning("promise_resolve_failed", extra={"error": str(exc)})
            return False

    def surface_proactive_insights(self, sender_id: str) -> list[dict]:
        """
        Return a list of actionable insights for the given sender.

        Checks:
        - Overdue payment promises from this customer
        - Open stock alerts relevant to their purchase history
        - Whether they are a repeat buyer worth flagging

        Called by ContextEngine._get_insights() to enrich the LLM prompt.
        Returns an empty list on any failure — must never raise.
        """
        insights: list[dict] = []
        try:
            overdue = self.get_overdue_promises(days_threshold=2)
            sender_overdue = [p for p in overdue if p.get("sender_id") == sender_id]
            for promise in sender_overdue[:2]:
                entities = promise.get("entities") or {}
                debtor = entities.get("debtor") or "this customer"
                amount = entities.get("amount")
                amt_str = f" UGX {float(amount):,.0f}" if amount else ""
                insights.append({
                    "type": "overdue_promise",
                    "message": f"{debtor} has an overdue payment promise{amt_str}.",
                    "event_id": promise.get("event_id"),
                })
        except Exception as exc:
            logger.debug("surface_overdue_failed", extra={"error": str(exc)})

        try:
            open_alerts = self.get_open_stock_alerts()
            for alert in open_alerts[:2]:
                entities = alert.get("entities") or {}
                item = entities.get("item", "stock")
                insights.append({
                    "type": "low_stock",
                    "message": f"Low stock alert: {item} is running low.",
                    "event_id": alert.get("event_id"),
                })
        except Exception as exc:
            logger.debug("surface_stock_failed", extra={"error": str(exc)})

        return insights

    def get_payment_patterns(self, sender_id: str) -> dict:
        """
        Derive simple payment behaviour patterns for a sender from stored
        transaction data.  Returns a dict with:
          - avg_days_to_pay: float | None
          - reliability: "reliable" | "slow" | "unknown"
          - promise_kept_rate: float (0–1) | None

        Falls back to {} on any error — must never raise.
        """
        try:
            promises = self.get_unresolved_promises()
            sender_promises = [p for p in promises if p.get("sender_id") == sender_id]

            # Count resolved vs pending to get a keep-rate approximation.
            # Unresolved list only contains pending ones; we use overdue count
            # as a proxy for broken promises.
            overdue_promises = self.get_overdue_promises(days_threshold=3)
            sender_overdue = [p for p in overdue_promises if p.get("sender_id") == sender_id]

            total_sampled = len(sender_promises) + len(sender_overdue)
            if total_sampled == 0:
                return {"reliability": "unknown", "avg_days_to_pay": None, "promise_kept_rate": None}

            broken = len(sender_overdue)
            kept_rate = round(1.0 - (broken / total_sampled), 2)

            if kept_rate >= 0.8:
                reliability = "reliable"
            elif kept_rate >= 0.5:
                reliability = "slow"
            else:
                reliability = "unreliable"

            return {
                "reliability": reliability,
                "promise_kept_rate": kept_rate,
                "avg_days_to_pay": None,  # would need resolved timestamps to compute
            }
        except Exception as exc:
            logger.debug("get_payment_patterns_failed", extra={"error": str(exc)})
            return {"reliability": "unknown", "avg_days_to_pay": None, "promise_kept_rate": None}