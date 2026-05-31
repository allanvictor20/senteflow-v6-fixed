"""
SenteFlow AI — Entity Resolver
================================
Resolves whether a name in a message refers to an existing known
customer or is someone new. Uses exact then fuzzy matching.
"""

import logging
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


class EntityResolver:

    def __init__(self, repo, org_id: str):
        self.repo = repo
        self.org_id = org_id

    def resolve_customer(self, name: str) -> dict:
        """
        Returns:
        {
          "resolved": True/False,
          "customer": { ... } or None,
          "candidates": [...],
          "needs_clarification": True/False,
          "clarification_question": "..." or None,
          "is_new": True/False,
        }
        """
        if not name or not name.strip():
            return {"resolved": False, "customer": None, "candidates": [],
                    "needs_clarification": False, "is_new": False}

        try:
            all_customers = (
                self.repo._db.collection("organizations").document(self.org_id)
                .collection("customers").get()
            )
        except Exception as exc:
            logger.warning("entity_resolve_fetch_failed", extra={"error": str(exc)})
            return {"resolved": False, "customer": None, "candidates": [],
                    "needs_clarification": False, "is_new": False}

        exact = []
        fuzzy = []

        for doc in all_customers:
            data = {**doc.to_dict(), "id": doc.id}
            display = data.get("display_name", "")
            aliases = data.get("aliases", [])
            all_names = [display] + aliases

            if any(n.lower().strip() == name.lower().strip() for n in all_names):
                exact.append(data)
                continue

            best_score = max((_similarity(name, n) for n in all_names), default=0)
            if best_score >= 0.75:
                fuzzy.append((best_score, data))

        if len(exact) == 1:
            return {"resolved": True, "customer": exact[0], "candidates": [],
                    "needs_clarification": False, "is_new": False}

        if len(exact) > 1:
            question = (
                f"I know multiple people named {name}. Did you mean: "
                + " or ".join(c.get("display_name", "") for c in exact) + "?"
            )
            return {"resolved": False, "customer": None, "candidates": exact,
                    "needs_clarification": True, "clarification_question": question,
                    "is_new": False}

        fuzzy_sorted = sorted(fuzzy, key=lambda x: x[0], reverse=True)

        if len(fuzzy_sorted) == 1:
            score, customer = fuzzy_sorted[0]
            logger.info("entity_fuzzy_resolved", extra={
                "name": name, "matched": customer.get("display_name"), "score": score
            })
            self._add_alias(customer["id"], name)
            return {"resolved": True, "customer": customer, "candidates": [],
                    "needs_clarification": False, "is_new": False}

        if len(fuzzy_sorted) > 1:
            candidates = [c for _, c in fuzzy_sorted[:3]]
            question = f"Did you mean {candidates[0].get('display_name')} or someone else?"
            return {"resolved": False, "customer": None, "candidates": candidates,
                    "needs_clarification": True, "clarification_question": question,
                    "is_new": False}

        return {"resolved": False, "customer": None, "candidates": [],
                "needs_clarification": False, "is_new": True}

    def _add_alias(self, customer_id: str, alias: str) -> None:
        try:
            doc_ref = (
                self.repo._db.collection("organizations").document(self.org_id)
                .collection("customers").document(customer_id)
            )
            data = doc_ref.get().to_dict() or {}
            aliases = data.get("aliases", [])
            if alias not in aliases:
                aliases.append(alias)
                doc_ref.update({"aliases": aliases})
        except Exception as exc:
            logger.warning("alias_add_failed", extra={"error": str(exc)})