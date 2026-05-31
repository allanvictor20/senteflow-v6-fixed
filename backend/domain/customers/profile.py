"""
SenteFlow AI — CustomerProfile
=================================
Full customer memory model as specified in the architecture doc.

Replaces the thin Customer and CustomerMemory models with a single,
rich CustomerProfile that answers:
  - Who is this person?
  - What do we know about them?
  - How do they usually behave?
  - What should the business owner know about them?
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field
import uuid


class PaymentBehavior(str, Enum):
    IMMEDIATE = "immediate"          # Pays on the spot
    PROMPT = "prompt"                # Pays within 1-2 days
    RELIABLE = "reliable"            # Pays within a week
    SLOW = "slow"                    # Takes 2-4 weeks
    PROBLEMATIC = "problematic"      # Frequently misses payments
    UNKNOWN = "unknown"


class LoyaltyTier(str, Enum):
    NEW = "new"
    REGULAR = "regular"
    VIP = "vip"
    CHAMPION = "champion"


class CustomerProfile(BaseModel):
    """
    Single source of truth for everything we know about a customer.
    Built up automatically from every BusinessEvent interaction.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    org_id: str

    # Identity
    phone_number: Optional[str] = None
    display_name: str = "Unknown"
    business_name: Optional[str] = None
    location: Optional[str] = None
    aliases: list[str] = Field(default_factory=list)

    # Lifecycle
    first_seen_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    last_seen_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())

    # Financial summary
    total_orders: int = 0
    total_spend: float = 0.0        # cumulative confirmed spend
    total_paid: float = 0.0
    total_outstanding: float = 0.0
    average_order_value: float = 0.0

    # Behavioural signals
    payment_behavior: PaymentBehavior = PaymentBehavior.UNKNOWN
    typical_payment_days: int = 0   # average days to pay after promise
    preferred_products: list[str] = Field(default_factory=list)
    communication_style: str = "unknown"   # e.g. "formal", "casual", "terse"

    # Computed scores  (0.0–1.0)
    loyalty_score: float = 0.0
    risk_score: float = 0.0         # higher = more likely to default / churn

    # Relationship
    loyalty_tier: LoyaltyTier = LoyaltyTier.NEW
    refers_others: bool = False

    # Memory
    notes: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    open_promises: list[dict] = Field(default_factory=list)

    # AI-generated narrative (refreshed periodically)
    ai_summary: Optional[str] = None
    ai_summary_generated_at: Optional[str] = None

    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    metadata: dict[str, Any] = Field(default_factory=dict)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def to_ai_context(self) -> str:
        """Compact string injected into AI prompts for context."""
        parts = [f"Customer: {self.display_name}"]
        if self.total_orders:
            parts.append(f"Orders: {self.total_orders}")
        if self.total_spend:
            parts.append(f"Total spend: UGX {self.total_spend:,.0f}")
        if self.total_outstanding:
            parts.append(f"Outstanding: UGX {self.total_outstanding:,.0f}")
        if self.payment_behavior != PaymentBehavior.UNKNOWN:
            parts.append(f"Payment: {self.payment_behavior.value}")
        if self.preferred_products:
            parts.append(f"Usually buys: {', '.join(self.preferred_products[:3])}")
        if self.open_promises:
            parts.append(f"Open promises: {len(self.open_promises)}")
        if self.loyalty_tier != LoyaltyTier.NEW:
            parts.append(f"Tier: {self.loyalty_tier.value}")
        return " | ".join(parts)

    def months_as_customer(self) -> float:
        try:
            first = datetime.fromisoformat(self.first_seen_at.replace("Z", "+00:00"))
            delta = datetime.utcnow() - first.replace(tzinfo=None)
            return round(delta.days / 30, 1)
        except Exception:
            return 0.0

    def update_from_event(self, event_type: str, entities: dict) -> None:
        """Mutate this profile based on an incoming BusinessEvent."""
        now = datetime.utcnow().isoformat()
        self.last_seen_at = now
        self.updated_at = now

        amount = float(entities.get("amount") or 0)

        if event_type in ("payment_received", "payment", "income"):
            self.total_paid += amount
            self.total_outstanding = max(0.0, self.total_outstanding - amount)
            self._refresh_payment_behavior()

        elif event_type == "debt_created":
            self.total_outstanding += amount

        elif event_type in ("customer_order", "order_received"):
            self.total_orders += 1
            if amount:
                self.total_spend += amount
                prior = self.average_order_value * (self.total_orders - 1)
                self.average_order_value = round(
                    (prior + amount) / self.total_orders, 2
                )
            item = entities.get("item") or entities.get("product")
            if item and item not in self.preferred_products:
                self.preferred_products.append(item)
            self.preferred_products = self.preferred_products[:10]

        elif event_type == "payment_promise":
            self.open_promises.append({
                "type": "payment",
                "amount": amount,
                "due": entities.get("due_date"),
                "recorded_at": now,
            })

        self._refresh_scores()

    def _refresh_payment_behavior(self) -> None:
        if self.total_orders == 0:
            return
        ratio = self.total_paid / max(self.total_spend, 1)
        if ratio >= 0.99:
            self.payment_behavior = PaymentBehavior.IMMEDIATE
        elif ratio >= 0.85:
            self.payment_behavior = PaymentBehavior.PROMPT
        elif ratio >= 0.65:
            self.payment_behavior = PaymentBehavior.RELIABLE
        elif ratio >= 0.40:
            self.payment_behavior = PaymentBehavior.SLOW
        else:
            self.payment_behavior = PaymentBehavior.PROBLEMATIC

    def _refresh_scores(self) -> None:
        # Loyalty score: blend of orders, spend, and payment reliability
        order_factor = min(self.total_orders / 20, 1.0)
        payment_factor = (self.total_paid / max(self.total_spend, 1)) if self.total_spend else 0.5
        self.loyalty_score = round((order_factor * 0.5) + (payment_factor * 0.5), 3)

        # Risk score: driven by outstanding balance ratio
        if self.total_spend > 0:
            self.risk_score = round(
                min(self.total_outstanding / self.total_spend, 1.0), 3
            )

        # Loyalty tier
        if self.loyalty_score >= 0.8 and self.total_orders >= 10:
            self.loyalty_tier = LoyaltyTier.CHAMPION
        elif self.loyalty_score >= 0.6 or self.total_orders >= 5:
            self.loyalty_tier = LoyaltyTier.VIP
        elif self.total_orders >= 2:
            self.loyalty_tier = LoyaltyTier.REGULAR
        else:
            self.loyalty_tier = LoyaltyTier.NEW
