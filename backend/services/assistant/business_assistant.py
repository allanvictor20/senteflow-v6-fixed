"""
SenteFlow AI - Conversational business assistant.

Translates common WhatsApp business questions into repository reads.
This stays intentionally lightweight: deterministic query planning first,
with Gemini only needed later for more open-ended wording.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class AssistantAnswer:
    intent: str
    answer: str
    data: dict[str, Any]


class BusinessAssistant:
    def __init__(self, repo, org_id: str):
        self.repo = repo
        self.org_id = org_id

    def answer(self, question: str, sender_id: str | None = None) -> AssistantAnswer:
        text = (question or "").lower().strip()

        if any(phrase in text for phrase in ("hasn't paid", "hasnt paid", "unpaid", "who owes", "balance")):
            return self._unpaid_customers()
        if any(phrase in text for phrase in ("pending delivery", "deliveries", "delivery")):
            return self._pending_deliveries()
        if any(phrase in text for phrase in ("sold today", "sales today", "sell today", "today")):
            return self._sales_today()
        if "ordered" in text or "order" in text:
            return self._orders_for_question(text)
        if "interested" in text or "inquiries" in text or "leads" in text:
            return self._customer_interests(text)

        summary = self.repo.compute_financial_summary(self.org_id)
        return AssistantAnswer(
            intent="summary",
            answer=(
                f"Income: UGX {summary.total_income:,.0f}\n"
                f"Expenses: UGX {summary.total_expenses:,.0f}\n"
                f"Balance: UGX {summary.balance:,.0f}"
            ),
            data=summary.model_dump(),
        )

    def _unpaid_customers(self) -> AssistantAnswer:
        unpaid_orders = self.repo.list_orders(self.org_id, payment_status="unpaid", limit=100)
        customers = self.repo.list_customers(self.org_id, limit=100)
        debt_customers = [
            c for c in customers
            if float(c.get("outstanding_balance") or 0) > 0
        ]
        names = [
            o.get("customer_name") or o.get("customer_id")
            for o in unpaid_orders[:10]
        ] + [
            c.get("display_name") or c.get("sender_id")
            for c in debt_customers[:10]
        ]
        names = [n for n in dict.fromkeys(names) if n]
        answer = "No unpaid customers found." if not names else "Unpaid customers:\n" + "\n".join(f"- {n}" for n in names)
        return AssistantAnswer("unpaid_customers", answer, {
            "orders": unpaid_orders,
            "customers": debt_customers,
        })

    def _pending_deliveries(self) -> AssistantAnswer:
        orders = self.repo.list_orders(self.org_id, delivery_status="pending", limit=100)
        if not orders:
            answer = "No pending deliveries found."
        else:
            lines = []
            for order in orders[:10]:
                customer = order.get("customer_name") or order.get("customer_id") or "Unknown customer"
                item = order.get("items") or order.get("item") or "order"
                lines.append(f"- {customer}: {item}")
            answer = "Pending deliveries:\n" + "\n".join(lines)
        return AssistantAnswer("pending_deliveries", answer, {"orders": orders})

    async def _sales_today(self) -> AssistantAnswer:
        today = datetime.utcnow().date().isoformat()
        transactions = await self.repo.list_transactions(self.org_id, limit=300)
        todays_sales = [
            t for t in transactions
            if str(t.get("created_at", "")).startswith(today)
            and t.get("type") in ("income", "payment", "contribution", "payment_received")
        ]
        total = sum(float(t.get("amount") or 0) for t in todays_sales)
        return AssistantAnswer(
            "sales_today",
            f"Today you have recorded UGX {total:,.0f} from {len(todays_sales)} sale/payment records.",
            {"transactions": todays_sales, "total": total},
        )

    def _orders_for_question(self, text: str) -> AssistantAnswer:
        orders = self.repo.list_orders(self.org_id, limit=100)
        name_tokens = [token for token in text.split() if len(token) > 2 and token not in {"what", "did", "order", "ordered"}]
        if name_tokens:
            orders = [
                o for o in orders
                if any(token in str(o.get("customer_name", "")).lower() or token in str(o.get("customer_id", "")).lower() for token in name_tokens)
            ]
        if not orders:
            answer = "I couldn't find matching orders."
        else:
            answer = "Matching orders:\n" + "\n".join(
                f"- {(o.get('customer_name') or o.get('customer_id') or 'Customer')}: {o.get('items') or o.get('source_message') or 'order'}"
                for o in orders[:10]
            )
        return AssistantAnswer("orders_lookup", answer, {"orders": orders})

    def _customer_interests(self, text: str) -> AssistantAnswer:
        customers = self.repo.list_customers(self.org_id, limit=100)
        tokens = [token for token in text.split() if len(token) > 3 and token not in {"show", "customers", "interested"}]
        matches = customers
        if tokens:
            matches = [
                c for c in customers
                if any(token in str(c.get("last_item", "")).lower() or token in str(c.get("last_message", "")).lower() for token in tokens)
            ]
        if not matches:
            answer = "No matching customer interests found yet."
        else:
            answer = "Customer interests:\n" + "\n".join(
                f"- {c.get('display_name') or c.get('sender_id')}: {c.get('last_item') or c.get('last_message') or 'recent inquiry'}"
                for c in matches[:10]
            )
        return AssistantAnswer("customer_interests", answer, {"customers": matches})
