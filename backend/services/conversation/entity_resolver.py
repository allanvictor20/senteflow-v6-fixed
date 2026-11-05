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


def _tokens(name: str) -> list[str]:
    return [t for t in name.lower().replace(".", " ").split() if t]


def _part_match(query: str, known: str) -> bool:
    """
    "Brian" matches "Brian Okello"; "Okello" matches "Brian Okello";
    "Mama Brian" matches "Mama Brian Shop". Every word the owner used must
    appear in the known name.
    """
    q, k = _tokens(query), _tokens(known)
    return bool(q) and len(q) < len(k) and all(t in k for t in q)


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
            # Before customer resolution existed, the pipeline wrote one
            # "customer" per WhatsApp sender (the owner), keyed by their JID,
            # with a display_name that flipped on every message. Those
            # documents do not describe a real customer, so never match them.
            if "@" in doc.id:
                continue
            data = {**doc.to_dict(), "id": doc.id}
            display = data.get("display_name") or ""
            aliases = [a for a in (data.get("aliases") or []) if a]
            all_names = [n for n in [display] + aliases if n]
            if not all_names:
                continue

            if any(n.lower().strip() == name.lower().strip() for n in all_names):
                exact.append(data)
                continue

            if any(_part_match(name, n) for n in all_names):
                fuzzy.append((0.8, data))
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
            # Remember spelling variants ("Brain" → "Brian"), but not a bare
            # first name — that would make it an exact match for this person
            # even after a second "Brian" becomes a customer.
            if not _part_match(name, customer.get("display_name") or ""):
                self._add_alias(customer["id"], name)
            return {"resolved": True, "customer": customer, "candidates": [],
                    "needs_clarification": False, "is_new": False}

        if len(fuzzy_sorted) > 1:
            candidates = [c for _, c in fuzzy_sorted[:3]]
            names = [c.get("display_name", "") for c in candidates]
            question = f"Which {name} do you mean: " + ", ".join(names[:-1]) + f" or {names[-1]}?"
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