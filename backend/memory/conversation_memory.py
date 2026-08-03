"""Simple recent-interaction memory for WhatsApp conversations."""



from typing import Any
from utils.clock import utc_now


class ConversationMemory:
    """Hackathon-friendly memory: persist and retrieve the last N interactions."""

    def __init__(self, repo, org_id: str):
        self.repo = repo
        self.org_id = org_id

    def remember_message(
        self,
        sender_id: str,
        event_id: str,
        text: str | None,
        intent: str,
        extracted: dict[str, Any] | None = None,
    ) -> str:
        return self.repo.append_conversation_timeline(
            self.org_id,
            sender_id,
            {
                "event_id": event_id,
                "timestamp": utc_now().isoformat(),
                "message": text,
                "intent": intent,
                "extracted": extracted or {},
            },
        )

    def last_interactions(self, sender_id: str, limit: int = 10) -> list[dict]:
        return self.repo.list_conversation_timeline(self.org_id, sender_id, limit=limit)
