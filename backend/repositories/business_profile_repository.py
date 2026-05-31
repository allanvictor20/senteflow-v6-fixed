"""
SenteFlow — BusinessProfileRepository (IDEA 08)
Stores and retrieves per-org BusinessProfile from Firestore.
Path: organizations/{org_id}/profile/config
"""

import asyncio
import logging
from typing import Optional

from domain.business_profile.model import BusinessProfile

logger = logging.getLogger(__name__)


class BusinessProfileRepository:
    def __init__(self, db):
        self._db = db

    def _doc_ref(self, org_id: str):
        return (
            self._db.collection("organizations")
            .document(org_id)
            .collection("profile")
            .document("config")
        )

    async def get_profile(self, org_id: str) -> Optional[BusinessProfile]:
        try:
            doc = await asyncio.to_thread(self._doc_ref(org_id).get)
            if not doc.exists:
                return None
            return BusinessProfile(**doc.to_dict())
        except Exception as exc:
            logger.warning("profile_fetch_failed", extra={"error": str(exc), "org_id": org_id})
            return None

    async def save_profile(self, profile: BusinessProfile) -> None:
        try:
            await asyncio.to_thread(
                self._doc_ref(profile.org_id).set,
                profile.model_dump(mode="json"),
            )
        except Exception as exc:
            logger.error("profile_save_failed", extra={"error": str(exc), "org_id": profile.org_id})
            raise
