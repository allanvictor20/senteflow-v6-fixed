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
        entry = {
            "event_id": event_id,
            "timestamp": utc_now().isoformat(),
            "message": text,
            "intent": intent,
            # Keep only plain, small values — this is prompt context, not a record.
            "extracted": {
                k: v for k, v in (extracted or {}).items()
                if not str(k).startswith("_") and isinstance(v, (str, int, float, bool))
            },
        }
        # One document per interaction. Appending to an array on the
        # conversation document grew it without bound and would eventually
        # hit Firestore's 1 MiB document limit for an active owner.
        appender = getattr(self.repo, "append_interaction", None)
        if appender is not None:
            return appender(self.org_id, sender_id, entry)
        return self.repo.append_conversation_timeline(self.org_id, sender_id, entry)

    def last_interactions(self, sender_id: str, limit: int = 10) -> list[dict]:
        lister = getattr(self.repo, "list_interactions", None)
        if lister is not None:
            return lister(self.org_id, sender_id, limit=limit)
        return self.repo.list_conversation_timeline(self.org_id, sender_id, limit=limit)
