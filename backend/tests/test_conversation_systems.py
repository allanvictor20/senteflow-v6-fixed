from domain.events.business_event import BusinessEvent
from domain.events.event_types import EventType
from services.conversation import ConversationStateManager, EntityLinker


class FakeRepo:
    def __init__(self):
        self.conversations = {}
        self.timeline = {}
        self.links = {}
        self.orders = []

    def get_conversation(self, org_id, sender_id):
        return self.conversations.get(sender_id)

    def upsert_conversation(self, org_id, sender_id, updates):
        current = self.conversations.get(sender_id, {})
        self.conversations[sender_id] = {**current, **updates}
        return sender_id

    def append_conversation_timeline(self, org_id, sender_id, entry):
        self.timeline.setdefault(sender_id, []).append(entry)
        return entry["event_id"]

    def list_orders(self, org_id, limit=100, **filters):
        return self.orders[:limit]

    def save_entity_link(self, org_id, link):
        link_id = f"link-{len(self.links) + 1}"
        self.links[link_id] = link
        return link_id


def test_customer_order_moves_conversation_to_awaiting_payment():
    repo = FakeRepo()
    event = BusinessEvent(
        event_id="evt-order",
        event_type=EventType.CUSTOMER_ORDER,
        sender_id="256700000000@c.us",
        raw_message="I want 3 shoes",
        entities={"item": "shoes", "quantity": 3, "order_id": "order-1"},
        confidence=0.91,
    )

    manager = ConversationStateManager(repo, "org-1")
    snapshot = manager.apply_business_event(event)

    assert snapshot["state"] == "awaiting_payment"
    assert snapshot["pending_action"] == "customer_payment"
    assert snapshot["active_order_id"] == "order-1"
    assert repo.timeline[event.sender_id][0]["from_state"] == "pending_inquiry"
    assert repo.timeline[event.sender_id][0]["to_state"] == "awaiting_payment"


def test_payment_receipt_media_updates_existing_flow():
    repo = FakeRepo()
    repo.conversations["customer-1"] = {
        "state": "awaiting_payment",
        "active_order_id": "order-9",
    }

    manager = ConversationStateManager(repo, "org-1")
    snapshot = manager.record_media_processed(
        sender_id="customer-1",
        event_id="evt-receipt",
        media_id="media-1",
        transaction_ids=["txn-1"],
        summary="Receipt for UGX 150000",
    )

    assert snapshot["state"] == "payment_received"
    assert snapshot["pending_action"] == "delivery_preparation"
    assert repo.timeline["customer-1"][0]["related_transaction_ids"] == ["txn-1"]


def test_entity_linker_links_media_to_customer_conversation_order_and_transaction():
    repo = FakeRepo()
    repo.conversations["customer-1"] = {
        "state": "payment_received",
        "active_order_id": "order-9",
    }

    links = EntityLinker(repo, "org-1").link_media_extraction(
        sender_id="customer-1",
        event_id="evt-receipt",
        media_id="media-1",
        transaction_ids=["txn-1"],
    )

    relationships = {(link["target_entity_type"], link["relationship"]) for link in links}
    assert ("customer", "sent_by") in relationships
    assert ("conversation", "belongs_to") in relationships
    assert ("order", "evidence_for") in relationships
    assert ("transaction", "extracted") in relationships
