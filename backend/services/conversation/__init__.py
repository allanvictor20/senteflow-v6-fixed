"""
Conversation bounded context.

Re-exports the public collaborators so callers can import them from the
package root — `from services.conversation import ConversationStateManager`.
"""

from services.conversation.entity_linker import EntityLinker
from services.conversation.entity_resolver import EntityResolver
from services.conversation.state_machine import ConversationStateManager, StateTransition

__all__ = [
    "ConversationStateManager",
    "EntityLinker",
    "EntityResolver",
    "StateTransition",
]
