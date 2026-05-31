"""
SenteFlow — Dependency Injection (v5)
=======================================
Wires all repositories, services, and the WhatsApp client.

v5 additions:
- MemoryRepository injected for IDEA 03 (CustomerMemory fast path in ContextEngine)
- BusinessProfileRepository injected for IDEA 08 (per-org prompt injection)
- OrgConfig loaded for IDEA 09 (permission system)
"""

from dataclasses import dataclass
from typing import Optional
import os


@dataclass
class AppDependencies:
    db: object
    repo: object                        # ConversationAggregateRepository
    wa_client: Optional[object] = None  # EvolutionClient
    org_id: str = "default"
    mem_repo: Optional[object] = None   # MemoryRepository (IDEA 03)
    profile_repo: Optional[object] = None  # BusinessProfileRepository (IDEA 08)
    org_config: Optional[object] = None    # OrgConfig (IDEA 09)

    _message_router: object = None

    def message_router(self):
        if self._message_router is None:
            from integrations.whatsapp.message_router import MessageRouter
            self._message_router = MessageRouter(
                wa_client=self.wa_client,
                repo=self.repo,
                org_id=self.org_id,
                mem_repo=self.mem_repo,
                profile_repo=self.profile_repo,
                org_config=self.org_config,
            )
        return self._message_router


def build_dependencies(db, org_id: str = "default") -> AppDependencies:
    """
    Construct all dependencies from a Firestore db client.
    Called once at application startup.
    """
    from repositories.conversation_aggregate_repository import ConversationAggregateRepository
    from repositories.memory_repository import MemoryRepository
    from repositories.business_profile_repository import BusinessProfileRepository
    from domain.permissions.model import OrgConfig

    repo = ConversationAggregateRepository(db)
    mem_repo = MemoryRepository(db)
    profile_repo = BusinessProfileRepository(db)

    # OrgConfig: load defaults; in future this can be loaded from Firestore per-org
    org_config = OrgConfig.default(org_id)

    wa_url = os.environ.get("EVOLUTION_API_URL")
    wa_token = os.environ.get("EVOLUTION_API_TOKEN")
    wa_instance = os.environ.get("EVOLUTION_INSTANCE_NAME", "senteflow")

    wa_client = None
    if wa_url and wa_token:
        from integrations.whatsapp.client import EvolutionClient
        wa_client = EvolutionClient(
            base_url=wa_url,
            api_key=wa_token,
            session=wa_instance,
        )

    return AppDependencies(
        db=db,
        repo=repo,
        wa_client=wa_client,
        org_id=org_id,
        mem_repo=mem_repo,
        profile_repo=profile_repo,
        org_config=org_config,
    )
