"""
SenteFlow — ContextEngine (v5)
==============================
Two key upgrades from the stress-test analysis:

IDEA 03 — Memory Compaction (P1):
  _customer_context() now reads CustomerMemory first (1 Firestore read,
  one formatted line). Raw transaction recomputation is the fallback only
  for first-time senders with no memory yet.
  Before: 50 raw reads recomputing what CustomerMemory already stores.
  After:  1 read, instant context.

IDEA 10 — Parallel Context Loading (P1):
  get_context() now fires all 6 sub-fetches simultaneously with
  asyncio.gather(). Time drops from sum(all reads) to max(slowest read).
  Before: ~480ms sequential overhead per message.
  After:  ~80ms (slowest single read).
"""

import asyncio
import logging
import inspect
from typing import Any, Optional

logger = logging.getLogger(__name__)


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


async def _call(fn, *args, **kwargs):
    """
    Run a repository call without blocking the event loop.

    TransactionRepository is synchronous (each call is a blocking Firestore
    round-trip). Wrapping the calls in asyncio.to_thread is what lets the
    asyncio.gather below actually overlap them; without it the "parallel"
    fetches ran one after another.
    """
    result = await asyncio.to_thread(fn, *args, **kwargs)
    return await _maybe_await(result)


class ContextEngine:
    """
    Assembles context before event extraction.

    The WhatsApp sender is always the business owner, so before extraction we
    only know the owner's own conversation and the business as a whole.
    Customer-specific context (balance, open orders, promises) is loaded when
    a customer_id is known.
    """

    def __init__(self, repo, org_id: str, mem_repo=None):
        self.repo = repo
        self.org_id = org_id
        self.mem_repo = mem_repo  # MemoryRepository — injected for IDEA 03

    # ─── IDEA 10: parallel fetch ──────────────────────────────────────────────

    async def get_context(
        self,
        sender_id: str,
        raw_message: str = "",
        customer_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Build the context dict. Sub-fetches run concurrently.

        Returns:
        {
          "customer":            { name, outstanding_balance, risk_level, summary } (customer_id only),
          "conversation":        the owner's conversation with SenteFlow,
          "recent_events":       last 5 events the owner recorded,
          "open_orders":         open orders (this customer's, or the business's latest),
          "unresolved_promises": open payment promises (this customer's, or the business's),
          "entity_links":        links for this customer,
          "proactive_insights":  patterns worth surfacing,
          "payment_pattern":     { reliability, ... },
        }
        """
        keys = [
            "customer",
            "conversation",
            "open_orders",
            "recent_events",
            "unresolved_promises",
            "entity_links",
            "insights",
        ]

        async def _none(default):
            return default

        results = await asyncio.gather(
            self._customer_context(customer_id) if customer_id else _none({}),
            self._get_conversation(sender_id),
            self._get_open_orders(customer_id),
            self._recent_events(sender_id),
            self._unresolved_promises(customer_id),
            self._get_entity_links(customer_id) if customer_id else _none([]),
            self._get_insights(customer_id) if customer_id else _none({"proactive_insights": [], "payment_pattern": {}}),
            return_exceptions=True,
        )

        context: dict[str, Any] = {}
        defaults = [{}, {}, [], [], [], [], {"proactive_insights": [], "payment_pattern": {}}]

        for key, result, default in zip(keys, results, defaults):
            if isinstance(result, Exception):
                logger.warning(f"context_{key}_failed", extra={"error": str(result)})
                context[key] = default
            else:
                context[key] = result

        # Flatten insights into top-level keys expected by downstream callers
        insights = context.pop("insights", {}) or {}
        context["proactive_insights"] = insights.get("proactive_insights", [])
        context["payment_pattern"] = insights.get("payment_pattern", {})

        # The owner's pending-question bookkeeping is not business context.
        conversation = context.get("conversation") or {}
        context["conversation"] = {
            k: v for k, v in conversation.items()
            if not k.startswith("pending_") and k != "timeline"
        }

        logger.debug("context_built", extra={
            "sender": sender_id,
            "customer_id": customer_id,
            "recent_events": len(context.get("recent_events", [])),
            "source": context.get("customer", {}).get("_source", "none"),
        })
        return context

    # ─── IDEA 03: read CustomerMemory first ───────────────────────────────────

    async def _customer_context(self, customer_id: str) -> dict[str, Any]:
        """
        IDEA 03: Read CustomerMemory summary if it exists (fast path).
        Fall back to recomputing from the ledger otherwise.
        """
        if self.mem_repo:
            try:
                memory = await self.mem_repo.get_memory(self.org_id, customer_id)
                if memory and (memory.get("total_orders", 0) > 0 or memory.get("total_paid", 0) > 0
                               or memory.get("total_outstanding", 0) > 0):
                    from domain.business_memory.model import CustomerMemory
                    mem_obj = CustomerMemory(**memory)
                    outstanding = mem_obj.total_outstanding
                    return {
                        **memory,
                        "customer_id": customer_id,
                        "outstanding_balance": outstanding,
                        "total_paid": mem_obj.total_paid,
                        "risk_level": _risk(outstanding),
                        "total_transactions": mem_obj.total_orders,
                        "summary": mem_obj.to_ai_context(),
                        "_source": "customer_memory",  # debug marker
                    }
            except Exception as exc:
                logger.warning("customer_memory_read_failed", extra={"error": str(exc)})

        return await self._compute_from_transactions(customer_id)

    async def _compute_from_transactions(self, customer_id: str) -> dict[str, Any]:
        """Recompute a customer's position from the ledger."""
        profile = await _call(self.repo.get_customer_profile, self.org_id, customer_id) or {}
        txns = await _call(self.repo.list_transactions, self.org_id, limit=200)
        mine = [
            t for t in txns
            if t.get("customer_id") == customer_id or t.get("recorded_by") == customer_id
        ]

        def _amount(t):
            try:
                return float(t.get("amount") or 0)
            except (TypeError, ValueError):
                return 0.0

        def _kind(t):
            return t.get("transaction_type") or t.get("type") or t.get("event_type") or ""

        total_paid = sum(_amount(t) for t in mine if _kind(t) in ("income", "payment", "payment_received"))
        total_owed = sum(_amount(t) for t in mine if _kind(t) in ("debt_created",))
        outstanding = max(float(profile.get("outstanding_balance", 0) or 0), total_owed - total_paid, 0.0)

        return {
            **profile,
            "customer_id": customer_id,
            "outstanding_balance": outstanding,
            "total_paid": total_paid,
            "risk_level": _risk(outstanding),
            "total_transactions": len(mine),
            "_source": "transactions",  # debug marker
        }

    # ─── Sub-fetch helpers (called concurrently) ──────────────────────────────

    async def _get_conversation(self, sender_id: str) -> dict:
        return await _call(self.repo.get_conversation, self.org_id, sender_id) or {}

    async def _get_open_orders(self, customer_id: Optional[str]) -> list[dict]:
        terminal = {"completed", "cancelled", "delivered"}
        try:
            all_orders = await _call(self.repo.list_orders, self.org_id, limit=50)
        except Exception:
            return []
        open_orders = [o for o in all_orders if o.get("status") not in terminal]
        if customer_id:
            open_orders = [o for o in open_orders if o.get("customer_id") == customer_id]
        return open_orders[:5]

    async def _recent_events(self, sender_id: str, limit: int = 5) -> list[dict]:
        txns = await _call(self.repo.list_transactions, self.org_id, limit=50)
        mine = [t for t in txns if t.get("recorded_by") == sender_id or t.get("sender_id") == sender_id]
        return [
            {k: t.get(k) for k in ("transaction_type", "event_type", "amount", "customer_name", "payer", "description", "created_at") if t.get(k)}
            for t in mine[:limit]
        ]

    async def _unresolved_promises(self, customer_id: Optional[str]) -> list[dict]:
        lister = getattr(self.repo, "list_open_promises", None)
        if lister is None:
            return []
        try:
            promises = await _call(lister, self.org_id, limit=100)
        except Exception:
            return []
        if customer_id:
            promises = [
                p for p in promises
                if p.get("customer_id") == customer_id or (p.get("entities") or {}).get("customer_id") == customer_id
            ]
        return [
            {
                "debtor": (p.get("entities") or {}).get("customer") or (p.get("entities") or {}).get("debtor"),
                "amount": (p.get("entities") or {}).get("amount"),
                "due_date": (p.get("entities") or {}).get("due_date"),
            }
            for p in promises[:10]
        ]

    async def _get_entity_links(self, customer_id: str, limit: int = 25) -> list[dict]:
        """
        Relationships already recorded for this customer — which receipts,
        orders and transactions the system believes belong together.
        """
        finder = getattr(self.repo, "find_entity_links", None)
        if finder is None:
            return []
        try:
            return await _call(finder, self.org_id, "customer", customer_id, limit=limit) or []
        except Exception as exc:
            logger.warning("entity_links_fetch_failed", extra={"error": str(exc)})
            return []

    async def _get_insights(self, customer_id: str) -> dict:
        try:
            from services.memory.memory_engine import BusinessMemoryEngine
            memory = BusinessMemoryEngine(repo=self.repo, org_id=self.org_id)
            insights = await asyncio.to_thread(memory.surface_proactive_insights, customer_id)
            pattern = await asyncio.to_thread(memory.get_payment_patterns, customer_id)
            return {"proactive_insights": insights, "payment_pattern": pattern}
        except Exception as exc:
            logger.warning("insights_fetch_failed", extra={"error": str(exc)})
            return {"proactive_insights": [], "payment_pattern": {}}


def _risk(outstanding: float) -> str:
    if outstanding > 200_000:
        return "high"
    if outstanding > 50_000:
        return "medium"
    return "low"
