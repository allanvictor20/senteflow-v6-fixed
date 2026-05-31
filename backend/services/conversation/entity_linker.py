"""
Entity linking for WhatsApp business objects.

The linker records why a receipt, message, transaction, order, and customer are
treated as part of the same business flow.
"""

from __future__ import annotations

from typing import Any, Optional
import inspect

from domain.models import EntityLink


class EntityLinker:
    def __init__(self, repo, org_id: str):
        self.repo = repo
        self.org_id = org_id

    def link_business_event(self, event: Any) -> list[dict[str, Any]]:
        conversation = self.repo.get_conversation(self.org_id, event.sender_id) or {}
        if inspect.isawaitable(conversation):
            conversation = {}
        links: list[EntityLink] = [
            self._link(
                "business_event",
                event.event_id,
                "customer",
                event.sender_id,
                "sent_by",
                0.98,
                ["same WhatsApp sender"],
            ),
            self._link(
                "business_event",
                event.event_id,
                "conversation",
                event.sender_id,
                "belongs_to",
                0.96,
                ["same sender conversation"],
            ),
        ]

        order_id = event.entities.get("order_id") or conversation.get("active_order_id")
        if order_id:
            links.append(self._link(
                "business_event",
                event.event_id,
                "order",
                order_id,
                "updates",
                0.78,
                ["active order on conversation", "temporal match"],
            ))
        if event.transaction_id:
            links.append(self._link(
                "business_event",
                event.event_id,
                "transaction",
                event.transaction_id,
                "created",
                0.92,
                ["transaction created by event"],
            ))
        return self._persist_unique(links)

    def link_media_extraction(
        self,
        sender_id: str,
        event_id: str,
        media_id: str,
        transaction_ids: list[str],
        active_order_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        conversation = self.repo.get_conversation(self.org_id, sender_id) or {}
        if inspect.isawaitable(conversation):
            conversation = {}
        order_id = active_order_id or conversation.get("active_order_id")
        links: list[EntityLink] = [
            self._link("media_asset", media_id, "customer", sender_id, "sent_by", 0.98, ["same WhatsApp sender"]),
            self._link("media_asset", media_id, "conversation", sender_id, "belongs_to", 0.95, ["same sender conversation"]),
            self._link("media_asset", media_id, "business_event", event_id, "source_for", 0.90, ["same webhook event"]),
        ]
        if order_id:
            links.append(self._link("media_asset", media_id, "order", order_id, "evidence_for", 0.72, ["active order", "recent media"]))
        for transaction_id in transaction_ids:
            links.append(self._link("media_asset", media_id, "transaction", transaction_id, "extracted", 0.88, ["created from extraction"]))
            if order_id:
                links.append(self._link("transaction", transaction_id, "order", order_id, "pays_for", 0.70, ["active order", "same customer"]))
        return self._persist_unique(links)

    def _link(
        self,
        source_type: str,
        source_id: str,
        target_type: str,
        target_id: str,
        relationship: str,
        confidence: float,
        reasons: list[str],
    ) -> EntityLink:
        return EntityLink(
            source_entity_type=source_type,
            source_entity_id=source_id,
            target_entity_type=target_type,
            target_entity_id=target_id,
            relationship=relationship,
            confidence=confidence,
            reasons=reasons,
        )

    def _persist_unique(self, links: list[EntityLink]) -> list[dict[str, Any]]:
        persisted: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str, str, str]] = set()
        for link in links:
            key = (
                link.source_entity_type,
                link.source_entity_id,
                link.target_entity_type,
                link.target_entity_id,
                link.relationship,
            )
            if key in seen:
                continue
            seen.add(key)
            data = link.model_dump(mode="json")
            data["link_id"] = self.repo.save_entity_link(self.org_id, data)
            persisted.append(data)
        return persisted
