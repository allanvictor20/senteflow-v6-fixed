"""
SenteFlow AI — CustomerMemoryService
======================================
Bounded context: Customer Memory.

Every BusinessEvent that involves a customer updates their CustomerProfile.
Responsibilities:
  1. get_or_create_profile
  2. apply_event
  3. generate_ai_summary
  4. get_context_for_ai
"""

import logging
from datetime import datetime, timedelta

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from repositories.customer_profile_repository import CustomerProfileRepository

from domain.customers.profile import CustomerProfile
from utils.clock import utc_now

logger = logging.getLogger(__name__)

_SUMMARY_REFRESH_HOURS = 24


class CustomerMemoryService:

    def __init__(self, profile_repo, llm_client=None, gemini_client=None):
        """
        Args:
            profile_repo: CustomerProfileRepository instance.
            llm_client: optional async callable with a .generate(prompt, max_tokens=...)
                method. Used to refresh ai_summary on customer profiles. If not
                provided, summaries are skipped.
            gemini_client: DEPRECATED alias for llm_client (kept for backward
                compatibility — SenteFlow now uses Groq as the primary LLM).
        """
        self._repo = profile_repo
        self._llm = llm_client or gemini_client

    def apply_event(
        self,
        org_id: str,
        sender_id: str,
        sender_name: str,
        event_type: str,
        entities: dict,
    ) -> CustomerProfile:
        profile = self._repo.get_or_create(org_id, sender_id, sender_name)

        if sender_name and sender_name != "Unknown" and profile.display_name in ("Unknown", ""):
            profile.display_name = sender_name

        profile.update_from_event(event_type, entities)
        self._repo.upsert(org_id, profile)

        logger.info(
            "customer_memory_applied",
            extra={"customer_id": profile.id, "event_type": event_type},
        )
        return profile

    def get_context_for_ai(self, org_id: str, sender_id: str) -> str:
        profile = self._repo.get_by_phone(org_id, sender_id)
        if not profile:
            return ""
        return profile.to_ai_context()

    async def refresh_ai_summary(self, org_id: str, profile_id: str) -> Optional[str]:
        profile = self._repo.get(org_id, profile_id)
        if not profile:
            return None

        if profile.ai_summary_generated_at:
            try:
                last = datetime.fromisoformat(
                    profile.ai_summary_generated_at.replace("Z", "+00:00")
                )
                if utc_now() - last.replace(tzinfo=None) < timedelta(hours=_SUMMARY_REFRESH_HOURS):
                    return profile.ai_summary
            except Exception:
                pass

        if not self._llm:
            return None

        prompt = _build_summary_prompt(profile)
        try:
            summary = await self._llm.generate(prompt, max_tokens=200)
            self._repo.update_ai_summary(org_id, profile_id, summary)
            return summary
        except Exception as exc:
            logger.warning("ai_summary_failed", extra={"error": str(exc)})
            return None

    def get_at_risk_customers(self, org_id: str, risk_threshold: float = 0.6) -> list:
        return [p for p in self._repo.list(org_id, limit=200) if p.risk_score >= risk_threshold]

    def get_high_value_customers(self, org_id: str, min_spend: float = 500_000) -> list:
        return [p for p in self._repo.list(org_id) if p.total_spend >= min_spend]


def _build_summary_prompt(profile: CustomerProfile) -> str:
    months = profile.months_as_customer()
    return f"""Generate a 3-4 sentence customer summary for a Ugandan SME business owner.
Write in plain English. Be specific and actionable.

Customer data:
  Name: {profile.display_name}
  Business: {profile.business_name or 'not recorded'}
  Customer for: {months} months
  Total orders: {profile.total_orders}
  Total spend: UGX {profile.total_spend:,.0f}
  Average order: UGX {profile.average_order_value:,.0f}
  Outstanding debt: UGX {profile.total_outstanding:,.0f}
  Payment behavior: {profile.payment_behavior.value}
  Preferred products: {', '.join(profile.preferred_products[:5]) or 'not recorded'}
  Open promises: {len(profile.open_promises)}

Write the summary now. No labels or bullet points."""
