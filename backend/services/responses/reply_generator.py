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
    amount = float(amount or 0)
    if amount < 0:
        return "-" + _fmt_amount(-amount, currency)
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


# ─── Summary Reply ────────────────────────────────────────────────────────────

def generate_summary_reply(summary: FinancialSummary, currency: str = "UGX") -> str:
    """
    Format a financial summary for WhatsApp.
    """
    balance_emoji = "📈" if summary.balance >= 0 else "📉"
    balance_sign = "+" if summary.balance >= 0 else ""

    lines = [
        "📊 *Financial Summary*\n",
        f"💚 Income:   {_fmt_amount(summary.total_income, currency)}",
        f"🔴 Expenses: {_fmt_amount(summary.total_expenses, currency)}",
        f"{balance_emoji} Balance:  {balance_sign}{_fmt_amount(summary.balance, currency)}\n",
    ]

    if summary.members_paid > 0:
        lines.append(f"👥 Members paid: {summary.members_paid}")
    if summary.members_pending > 0:
        lines.append(f"⏳ Members pending: {summary.members_pending}")
        lines.append(f"   Outstanding: {_fmt_amount(summary.pending_amount, currency)}")

    if summary.categories:
        lines.append("\n📂 *Top Categories:*")
        sorted_cats = sorted(summary.categories.items(), key=lambda x: x[1], reverse=True)
        for cat, amount in sorted_cats[:5]:
            lines.append(f"  • {cat.replace('_', ' ').title()}: {_fmt_amount(amount, currency)}")

    return "\n".join(lines)


# ─── Recent Transactions Reply ────────────────────────────────────────────────

def generate_recent_reply(transactions: list[dict]) -> str:
    """
    Format recent transactions for WhatsApp.
    """
    # Only ledger entries carry a top-level amount; the BusinessEvent records
    # stored alongside them would otherwise show up as "UGX 0" lines.
    transactions = [t for t in transactions if _as_float(t.get("amount")) and t.get("status") != "rejected"]
    if not transactions:
        return "📋 No transactions recorded yet.\n\nSend a receipt photo or voice note to get started!"

    lines = [f"📋 *Last {min(len(transactions), 10)} Transactions*\n"]

    for txn in transactions[:10]:
        amount = _as_float(txn.get("amount"))
        currency = txn.get("currency") or "UGX"
        desc = str(txn.get("description") or "Transaction")[:40]
        person = txn.get("customer_name") or txn.get("payer") or txn.get("payee") or ""
        date = str(txn.get("date") or txn.get("created_at") or "")[:10]  # YYYY-MM-DD only
        txn_type = txn.get("transaction_type") or txn.get("event_type") or ""

        # Income vs expense indicator
        emoji = "💚" if txn_type in ("income", "contribution", "payment", "payment_received") else (
            "📒" if txn_type == "debt_created" else "🔴"
        )
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

def _as_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


_DEBT_TYPES = {"debt_created", "loan", "withdrawal"}
_PAYMENT_TYPES = {"payment", "payment_received", "income", "loan_repayment", "contribution"}


def compute_customer_balances(transactions: list[dict]) -> dict[str, dict]:
    """
    Outstanding balance per customer from the ledger: debts minus payments.

    Entries are grouped by customer_id when they have one, otherwise by name.
    Name-only entries (written before customer resolution existed) are folded
    into the matching customer_id when any entry links that name to an id.
    """
    rows = [t for t in transactions if t.get("status") != "rejected"]

    name_to_id: dict[str, str] = {}
    for t in rows:
        cid = t.get("customer_id")
        name = (t.get("customer_name") or t.get("debtor") or t.get("payer") or "").strip().lower()
        if cid and name:
            name_to_id.setdefault(name, cid)

    balances: dict[str, dict] = {}
    for t in rows:
        txn_type = t.get("transaction_type") or t.get("event_type") or t.get("type") or ""
        if txn_type in _DEBT_TYPES:
            sign = 1
            name = t.get("customer_name") or t.get("debtor") or t.get("payer") or t.get("member_name")
        elif txn_type in _PAYMENT_TYPES:
            sign = -1
            name = t.get("customer_name") or t.get("payer") or t.get("member_name")
        else:
            continue
        name = (name or "").strip()
        if not name and not t.get("customer_id"):
            continue
        key = t.get("customer_id") or name_to_id.get(name.lower()) or name.lower()
        entry = balances.setdefault(key, {"name": name or key, "owed": 0.0, "paid": 0.0})
        if name and (not entry["name"] or entry["name"] == key):
            entry["name"] = name
        amount = _as_float(t.get("amount"))
        if sign > 0:
            entry["owed"] += amount
        else:
            entry["paid"] += amount

    for entry in balances.values():
        entry["balance"] = round(entry["owed"] - entry["paid"], 2)
    return balances


def generate_debt_reply(
    transactions: list[dict],
    name_query: Optional[str] = None,
    open_promises: Optional[list[dict]] = None,
    currency: str = "UGX",
) -> str:
    """
    "Who owes me?" / "How much does Brian owe?" from the ledger.

    Debts come from debt_created entries (and legacy loan/withdrawal entries);
    payments reduce them. Open payment promises are listed separately because
    a promise is not always backed by a recorded debt.
    """
    balances = compute_customer_balances(transactions)
    promises = []
    for p in open_promises or []:
        entities = p.get("entities") or {}
        who = entities.get("customer") or entities.get("debtor") or p.get("debtor") or "Someone"
        promises.append({
            "name": str(who),
            "amount": _as_float(entities.get("amount") or p.get("amount")),
            "due": entities.get("due_date") or p.get("due_date_display") or "",
        })

    if name_query:
        q = name_query.lower().strip()
        matched = {k: v for k, v in balances.items() if q in v["name"].lower()}
        matched_promises = [p for p in promises if q in p["name"].lower()]
        if not matched and not matched_promises:
            return f"No records found for '{name_query}'."
        lines = [f"💳 *Balance for '{name_query}'*\n"]
        for entry in matched.values():
            if entry["balance"] > 0:
                lines.append(f"  {entry['name']}: owes {_fmt_amount(entry['balance'], currency)}")
            elif entry["balance"] < 0:
                lines.append(f"  {entry['name']}: in credit {_fmt_amount(-entry['balance'], currency)}")
            else:
                lines.append(f"  {entry['name']}: fully paid ✅")
            lines.append(f"     (debts {_fmt_amount(entry['owed'], currency)} · paid {_fmt_amount(entry['paid'], currency)})")
        for p in matched_promises:
            due = f" by {p['due']}" if p["due"] else ""
            amt = f" {_fmt_amount(p['amount'], currency)}" if p["amount"] else ""
            lines.append(f"  📅 Promised to pay{amt}{due}")
        return "\n".join(lines)

    outstanding = sorted(
        (v for v in balances.values() if v["balance"] > 0),
        key=lambda v: v["balance"],
        reverse=True,
    )
    if not outstanding and not promises:
        return "✅ No outstanding debts found. Everyone is up to date."

    lines: list[str] = []
    if outstanding:
        total = sum(v["balance"] for v in outstanding)
        lines.append(
            f"⚠️ *Outstanding debts ({len(outstanding)} customer{'s' if len(outstanding) != 1 else ''})*"
            f" — total {_fmt_amount(total, currency)}\n"
        )
        for v in outstanding[:15]:
            lines.append(f"  • {v['name']}: {_fmt_amount(v['balance'], currency)}")
        if len(outstanding) > 15:
            lines.append(f"  _... and {len(outstanding) - 15} more_")
    if promises:
        if lines:
            lines.append("")
        lines.append("📅 *Open payment promises*")
        for p in promises[:10]:
            due = f" by {p['due']}" if p["due"] else ""
            amt = f" {_fmt_amount(p['amount'], currency)}" if p["amount"] else ""
            lines.append(f"  • {p['name']}{amt}{due}")
    return "\n".join(lines)
