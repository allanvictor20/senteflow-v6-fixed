"""
SenteFlow AI - Operational Intelligence
=======================================
Business intelligence for SME WhatsApp businesses.

Functions in this file read data and return insights. They do not call AI or
write to the database.
"""

from datetime import datetime, timedelta


def _parse_dt(value):
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def detect_lost_customers(customers: list[dict], days_threshold: int = 45) -> list[dict]:
    cutoff = datetime.utcnow() - timedelta(days=days_threshold)
    lost = []
    for customer in customers:
        last_dt = _parse_dt(customer.get("last_interaction") or customer.get("last_contact"))
        if last_dt and last_dt < cutoff:
            lost.append({
                "customer_id": customer.get("customer_id") or customer.get("id"),
                "display_name": customer.get("display_name"),
                "days_since_contact": (datetime.utcnow() - last_dt).days,
                "outstanding_balance": customer.get("outstanding_balance", customer.get("total_owed", 0)),
            })
    return sorted(lost, key=lambda item: item["days_since_contact"], reverse=True)


def detect_repeat_customers(events: list[dict], min_purchases: int = 3) -> list[dict]:
    counts: dict[str, dict] = {}
    for event in events:
        event_type = event.get("event_type") or event.get("type")
        if event_type not in {"customer_order", "order_received", "payment_received", "payment", "income"}:
            continue
        customer_id = event.get("related_customer_id") or event.get("sender_id") or event.get("recorded_by")
        if not customer_id:
            continue
        counts.setdefault(customer_id, {"customer_id": customer_id, "event_count": 0, "total_value": 0.0})
        counts[customer_id]["event_count"] += 1
        counts[customer_id]["total_value"] += float((event.get("entities") or {}).get("amount") or event.get("amount") or 0)
    return [value for value in counts.values() if value["event_count"] >= min_purchases]


def detect_overdue_debts(events: list[dict]) -> list[dict]:
    today = datetime.utcnow()
    overdue = []
    for event in events:
        if (event.get("event_type") or event.get("type")) != "payment_promise":
            continue
        entities = event.get("entities") or {}
        due = _parse_dt(entities.get("due_date") or event.get("due_date"))
        if due and due < today:
            overdue.append({
                "event_id": event.get("event_id") or event.get("id"),
                "debtor": entities.get("debtor") or entities.get("customer"),
                "amount": entities.get("amount"),
                "days_overdue": (today - due).days,
                "due_date": due.isoformat(),
            })
    return sorted(overdue, key=lambda item: item["days_overdue"], reverse=True)


def detect_inventory_risk(events: list[dict]) -> list[dict]:
    cutoff = datetime.utcnow() - timedelta(days=7)
    low_stock_items: dict[str, dict] = {}
    restocked_items: set[str] = set()

    for event in sorted(events, key=lambda item: str(item.get("timestamp") or item.get("created_at") or "")):
        event_type = event.get("event_type") or event.get("type")
        entities = event.get("entities") or {}
        item = str(entities.get("item") or event.get("item") or "").lower()
        if not item:
            continue
        ts = _parse_dt(event.get("timestamp") or event.get("created_at"))
        if not ts:
            continue
        if event_type == "low_stock_alert" and ts > cutoff:
            low_stock_items[item] = {
                "item": item,
                "quantity": entities.get("quantity"),
                "flagged_at": ts.isoformat(),
            }
        elif event_type == "inventory_update":
            restocked_items.add(item)

    return [value for key, value in low_stock_items.items() if key not in restocked_items]


def detect_revenue_trends(events: list[dict]) -> dict:
    now = datetime.utcnow()
    week_ago = now - timedelta(days=7)
    two_weeks_ago = now - timedelta(days=14)
    this_week = 0.0
    last_week = 0.0

    for event in events:
        if (event.get("event_type") or event.get("type")) not in {"payment_received", "payment", "income"}:
            continue
        amount = float((event.get("entities") or {}).get("amount") or event.get("amount") or 0)
        ts = _parse_dt(event.get("timestamp") or event.get("created_at"))
        if not ts:
            continue
        if ts >= week_ago:
            this_week += amount
        elif ts >= two_weeks_ago:
            last_week += amount

    change_pct = ((this_week - last_week) / last_week) * 100 if last_week > 0 else 0.0
    return {
        "this_week": this_week,
        "last_week": last_week,
        "change_percent": round(change_pct, 1),
        "trend": "up" if change_pct > 5 else "down" if change_pct < -5 else "stable",
    }


def detect_supplier_dependence(events: list[dict]) -> list[dict]:
    supplier_counts: dict[str, int] = {}
    total = 0
    for event in events:
        event_type = event.get("event_type") or event.get("type")
        if event_type not in {"supplier_message", "expense_recorded"}:
            continue
        supplier = (event.get("entities") or {}).get("supplier") or event.get("supplier")
        if not supplier:
            continue
        total += 1
        key = str(supplier).strip()
        supplier_counts[key] = supplier_counts.get(key, 0) + 1
    if total == 0:
        return []
    return [
        {"supplier": supplier, "share_percent": round((count / total) * 100, 1), "event_count": count}
        for supplier, count in supplier_counts.items()
        if count / total > 0.5
    ]


def detect_followups_needed(events: list[dict]) -> list[dict]:
    followup_types = {"payment_promise", "debt_created", "customer_order", "complaint", "follow_up_required"}
    return [
        event for event in events
        if (event.get("event_type") or event.get("type")) in followup_types
        and (event.get("status") or event.get("processing_status")) not in {"completed", "resolved"}
    ]
