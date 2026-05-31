"""
SenteFlow — GenerateReplyWorkflow
====================================
Generates a contextual WhatsApp reply for a processed BusinessEvent.
Delegates to the response templates in ActionDispatcher for deterministic replies,
and to the LLM for open-ended queries.
"""

from domain.events import BusinessEvent


async def generate_reply(event: BusinessEvent, repo, org_id: str) -> str:
    """Return the WhatsApp reply string for a processed event."""
    from services.actions.action_dispatcher import ActionDispatcher
    dispatcher = ActionDispatcher(repo=repo, org_id=org_id)
    result = await dispatcher.dispatch(event)
    return result.whatsapp_reply
