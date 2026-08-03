"""
SenteFlow — Dependency Injection (v6)
=======================================
Wires all repositories, services, and the WhatsApp client.

`repo` is the TransactionRepository facade. That choice matters: the message
router, action dispatcher, context engine, reminder sender and legacy API
routes all call the facade's wide surface (list_transactions, save_media_asset,
enqueue_webhook_event, ...). The per-aggregate repositories are exposed
separately for the routers that were written against them.

v5 additions:
- MemoryRepository injected for IDEA 03 (CustomerMemory fast path in ContextEngine)
- BusinessProfileRepository injected for IDEA 08 (per-org prompt injection)
- OrgConfig loaded for IDEA 09 (permission system)
"""

from dataclasses import dataclass, field
from typing import Optional
import os


@dataclass
class AppDependencies:
    db: object
    repo: object                        # TransactionRepository (shared facade)
    wa_client: Optional[object] = None  # EvolutionClient
    org_id: str = "default"
    mem_repo: Optional[object] = None   # MemoryRepository (IDEA 03)
    profile_repo: Optional[object] = None  # BusinessProfileRepository (IDEA 08)
    org_config: Optional[object] = None    # OrgConfig (IDEA 09)

    # Per-aggregate repositories used by the /api routers
    conv_agg_repo: Optional[object] = None
    order_repo: Optional[object] = None
    task_repo: Optional[object] = None
    customer_profile_repo: Optional[object] = None
    customer_memory_svc: Optional[object] = None
    order_svc: Optional[object] = None

    _message_router: object = field(default=None, repr=False)

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
    from repositories.transaction_repository import TransactionRepository
    from repositories.conversation_aggregate_repository import ConversationAggregateRepository
    from repositories.customer_profile_repository import CustomerProfileRepository
    from repositories.memory_repository import MemoryRepository
    from repositories.business_profile_repository import BusinessProfileRepository
    from repositories.order_repository import OrderRepository
    from repositories.task_repository import TaskRepository
    from services.memory.customer_memory_service import CustomerMemoryService
    from services.orders.order_service import OrderService
    from domain.permissions.model import OrgConfig

    repo = TransactionRepository(db)
    conv_agg_repo = ConversationAggregateRepository(db)
    mem_repo = MemoryRepository(db)
    profile_repo = BusinessProfileRepository(db)
    order_repo = OrderRepository(db)
    task_repo = TaskRepository(db)
    customer_profile_repo = CustomerProfileRepository(db)
    customer_memory_svc = CustomerMemoryService(customer_profile_repo)
    order_svc = OrderService(order_repo)

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
        conv_agg_repo=conv_agg_repo,
        order_repo=order_repo,
        task_repo=task_repo,
        customer_profile_repo=customer_profile_repo,
        customer_memory_svc=customer_memory_svc,
        order_svc=order_svc,
    )
