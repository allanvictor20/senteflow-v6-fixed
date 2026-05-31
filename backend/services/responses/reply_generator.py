"""
SenteFlow AI — WhatsApp Reply Generator
=========================================
Generates conversational, WhatsApp-formatted reply messages.
Keeps replies concise, readable on a phone, and in plain text (no HTML).

WhatsApp formatting supported:
  *bold*      — wrap in asterisks
  _italic_    — wrap in underscores
  ~strikethrough~
  ```code```  — monospace

All replies are in English by default.
Amount formatting defaults to Ugandan locale (UGX, comma thousands separator).
"""

import logging
from typing import Optional

from domain.models import ExtractionResult, FinancialSummary

logger = logging.getLogger(__name__)


def _fmt_amount(amount: float, currency: str = "UGX") -> str:
    """Format amount as human-readable string."""
    if amount >= 1_000_000:
        return f"{currency} {amount/1_000_000:.1f}M"
    elif amount >= 1_000:
        return f"{currency} {amount:,.0f}"
    return f"{currency} {amount:.0f}"


# ─── Extraction Reply ─────────────────────────────────────────────────────────

async def generate_extraction_reply(result: ExtractionResult, saved_ids: list[str]) -> str:
    """
    Generate a confirmation reply after processing a receipt/voice note.
    """
    count = len(result.transactions)
    saved_count = len(saved_ids)

    if saved_count == 0 and count > 0:
        return (
            "⚠️ Found transactions but they appear to be duplicates already recorded.\n"
            "No new records saved."
        )

    lines = [f"✅ *{saved_count} transaction{'s' if saved_count != 1 else ''} recorded*\n"]

    for i, txn in enumerate(result.transactions[:5], 1):  # Cap at 5 for readability
        amount_str = _fmt_amount(txn.amount, txn.currency)
        person = txn.payer or txn.payee or txn.member_name or "Unknown"
        lines.append(f"{i}. {txn.description}")
        lines.append(f"   💰 {amount_str} · 👤 {person}")
        if txn.date:
            lines.append(f"   📅 {txn.date}")
        lines.append("")

    if count > 5:
        lines.append(f"_... and {count - 5} more transactions_\n")

    if result.anomalies:
        lines.append(f"⚠️ *{len(result.anomalies)} flag(s):*")
        for anomaly in result.anomalies[:3]:
            lines.append(f"  • {anomaly}")
        lines.append("")

    lines.append('Type "summary" to see your updated balance.')
    return "\n".join(lines)


# ─── Transaction Recorded Reply ───────────────────────────────────────────────

async def generate_reply(event_type: str, data: object) -> str:
    """
    Generate a reply for a specific event type.
    """
    if event_type == "transaction_recorded":
        # from domain.models import Transaction  # DEPRECATED — use BusinessEvent
        if isinstance(data, Transaction):
            amount_str = _fmt_amount(data.amount, data.currency)
            person = data.payer or data.payee or "Unknown"
            return (
                f"✅ *Transaction recorded*\n\n"
                f"💰 {amount_str}\n"
                f"👤 {person}\n"
                f"📂 {data.category.replace('_', ' ').title()}\n"
                f"📝 {data.description}"
            )

    return "✅ Done."


# ─── Summary Reply ────────────────────────────────────────────────────────────

def generate_summary_reply(summary: FinancialSummary) -> str:
    """
    Format a financial summary for WhatsApp.
    """
    balance_emoji = "📈" if summary.balance >= 0 else "📉"
    balance_sign = "+" if summary.balance >= 0 else ""

    lines = [
        "📊 *Financial Summary*\n",
        f"💚 Income:   {_fmt_amount(summary.total_income)}",
        f"🔴 Expenses: {_fmt_amount(summary.total_expenses)}",
        f"{balance_emoji} Balance:  {balance_sign}{_fmt_amount(summary.balance)}\n",
    ]

    if summary.members_paid > 0:
        lines.append(f"👥 Members paid: {summary.members_paid}")
    if summary.members_pending > 0:
        lines.append(f"⏳ Members pending: {summary.members_pending}")
        lines.append(f"   Outstanding: {_fmt_amount(summary.pending_amount)}")

    if summary.categories:
        lines.append("\n📂 *Top Categories:*")
        sorted_cats = sorted(summary.categories.items(), key=lambda x: x[1], reverse=True)
        for cat, amount in sorted_cats[:5]:
            lines.append(f"  • {cat.replace('_', ' ').title()}: {_fmt_amount(amount)}")

    return "\n".join(lines)


# ─── Recent Transactions Reply ────────────────────────────────────────────────

def generate_recent_reply(transactions: list[dict]) -> str:
    """
    Format recent transactions for WhatsApp.
    """
    if not transactions:
        return "📋 No transactions recorded yet.\n\nSend a receipt photo or voice note to get started!"

    lines = [f"📋 *Last {min(len(transactions), 10)} Transactions*\n"]

    for txn in transactions[:10]:
        amount = txn.get("amount", 0)
        currency = txn.get("currency", "UGX")
        desc = txn.get("description", "Transaction")[:40]
        person = txn.get("payer") or txn.get("payee") or ""
        date = txn.get("date", "")[:10]  # YYYY-MM-DD only
        txn_type = txn.get("transaction_type", "")

        # Income vs expense indicator
        emoji = "💚" if txn_type in ("income", "contribution", "payment") else "🔴"
        amount_str = _fmt_amount(amount, currency)

        line = f"{emoji} {amount_str}"
        if person:
            line += f" · {person}"
        if date:
            line += f" · {date}"
        lines.append(line)
        lines.append(f"   _{desc}_")
        lines.append("")

    lines.append('Type "summary" for totals.')
    return "\n".join(lines)


# ─── Debt Reply ───────────────────────────────────────────────────────────────

def generate_debt_reply(transactions: list[dict], name_query: Optional[str] = None) -> str:
    """
    Compute and format outstanding debt information.
    """
    # Aggregate by payer
    balances: dict[str, float] = {}
    for txn in transactions:
        payer = (txn.get("payer") or txn.get("member_name") or "").strip()
        if not payer:
            continue
        amount = float(txn.get("amount", 0))
        txn_type = txn.get("transaction_type", "")
        if txn_type in ("contribution", "payment", "loan_repayment"):
            balances[payer] = balances.get(payer, 0) + amount
        elif txn_type in ("loan", "withdrawal"):
            balances[payer] = balances.get(payer, 0) - amount

    if not balances:
        return "No member payment data found."

    # Filter by name if provided
    if name_query:
        matched = {k: v for k, v in balances.items() if name_query.lower() in k.lower()}
        if matched:
            lines = [f"💳 *Balance for '{name_query}'*\n"]
            for name, balance in matched.items():
                sign = "+" if balance >= 0 else ""
                lines.append(f"  {name}: {sign}{_fmt_amount(balance)}")
            return "\n".join(lines)
        else:
            return f"No records found for '{name_query}'."

    # Show all outstanding (negative balance = owes money)
    outstanding = {k: v for k, v in balances.items() if v < 0}
    if not outstanding:
        return "✅ No outstanding debts found. All members are up to date."

    lines = [f"⚠️ *Outstanding Debts ({len(outstanding)} member{'s' if len(outstanding) != 1 else ''})*\n"]
    for name, balance in sorted(outstanding.items(), key=lambda x: x[1]):
        lines.append(f"  • {name}: {_fmt_amount(abs(balance))} owes")

    return "\n".join(lines)
