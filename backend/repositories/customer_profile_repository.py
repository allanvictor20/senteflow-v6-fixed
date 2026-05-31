"""
SenteFlow AI — CustomerProfile Repository
==========================================
Firestore persistence for the rich CustomerProfile model.
"""

import logging
from datetime import datetime
from typing import Optional

from domain.customers.profile import CustomerProfile

logger = logging.getLogger(__name__)


class CustomerProfileRepository:

    def __init__(self, db):
        self._db = db

    def _col(self, org_id: str):
        return (
            self._db.collection("organizations")
            .document(org_id)
            .collection("customer_profiles")
        )

    def upsert(self, org_id: str, profile: CustomerProfile) -> str:
        profile.org_id = org_id
        profile.updated_at = datetime.utcnow().isoformat()
        doc_ref = self._col(org_id).document(profile.id)
        doc_ref.set(profile.model_dump(mode="json"), merge=True)
        logger.debug("customer_profile_upserted", extra={"id": profile.id})
        return profile.id

    def get(self, org_id: str, profile_id: str) -> Optional[CustomerProfile]:
        doc = self._col(org_id).document(profile_id).get()
        if not doc.exists:
            return None
        return CustomerProfile(**doc.to_dict())

    def get_by_phone(self, org_id: str, phone: str) -> Optional[CustomerProfile]:
        docs = (
            self._col(org_id)
            .where("phone_number", "==", phone)
            .limit(1)
            .get()
        )
        for d in docs:
            return CustomerProfile(**d.to_dict())
        return None

    def find_by_name(self, org_id: str, name: str) -> list[CustomerProfile]:
        all_docs = self._col(org_id).limit(200).get()
        name_lower = name.lower().strip()
        results = []
        for d in all_docs:
            data = d.to_dict()
            if name_lower in (data.get("display_name") or "").lower():
                results.append(CustomerProfile(**data))
            elif any(name_lower in a.lower() for a in data.get("aliases", [])):
                results.append(CustomerProfile(**data))
        return results

    def list(self, org_id: str, limit: int = 100) -> list[CustomerProfile]:
        docs = self._col(org_id).order_by("last_seen_at", direction="DESCENDING").limit(limit).get()
        profiles = []
        for d in docs:
            try:
                profiles.append(CustomerProfile(**d.to_dict()))
            except Exception as e:
                logger.warning("customer_profile_parse_error", extra={"id": d.id, "error": str(e)})
        return profiles

    def get_or_create(self, org_id: str, phone: str, display_name: str) -> CustomerProfile:
        existing = self.get_by_phone(org_id, phone)
        if existing:
            return existing
        profile = CustomerProfile(
            org_id=org_id,
            phone_number=phone,
            display_name=display_name,
        )
        self.upsert(org_id, profile)
        return profile

    def update_ai_summary(self, org_id: str, profile_id: str, summary: str) -> None:
        self._col(org_id).document(profile_id).update({
            "ai_summary": summary,
            "ai_summary_generated_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
        })
