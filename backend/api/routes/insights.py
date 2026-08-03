"""
SenteFlow AI — Insights Route
================================
GET /api/insights?org_id=...

Returns AI-generated business intelligence:
  - Revenue trend
  - At-risk customers
  - Low inventory signals
  - Customers who haven't bought recently
  - Follow-up due counts
"""

import logging
from fastapi import APIRouter, Depends, HTTPException

from core.auth import verify_firebase_token, ensure_org_access

logger = logging.getLogger(__name__)

insights_router = APIRouter(prefix="/api/insights", tags=["insights"])

_profile_repo = None
_order_repo = None
_task_repo = None
_conv_agg_repo = None


def set_insights_dependencies(profile_repo, order_repo, task_repo, conv_agg_repo=None):
    global _profile_repo, _order_repo, _task_repo, _conv_agg_repo
    _profile_repo = profile_repo
    _order_repo = order_repo
    _task_repo = task_repo
    _conv_agg_repo = conv_agg_repo


@insights_router.get("")
async def get_insights(
    org_id: str,
    _token: dict = Depends(verify_firebase_token),
):
    ensure_org_access(_token, org_id)

    insights = []

    # At-risk customers
    try:
        at_risk = [p for p in _profile_repo.list(org_id) if p.risk_score >= 0.6]
        if at_risk:
            names = ", ".join(p.display_name for p in at_risk[:3])
            insights.append({
                "type": "churn_risk",
                "severity": "warning",
                "message": f"{len(at_risk)} customer(s) at risk of churn: {names}.",
            })
    except Exception as e:
        logger.warning("insights_at_risk_error", extra={"error": str(e)})

    # Overdue tasks
    try:
        overdue = _task_repo.list_overdue(org_id)
        if overdue:
            insights.append({
                "type": "overdue_tasks",
                "severity": "warning",
                "message": f"{len(overdue)} task(s) are overdue. Check the Tasks screen.",
            })
    except Exception as e:
        logger.warning("insights_overdue_error", extra={"error": str(e)})

    # Active unpaid orders
    try:
        from domain.orders.model import OrderStatus
        unpaid = _order_repo.list_by_status(org_id, OrderStatus.AWAITING_PAYMENT, limit=50)
        if unpaid:
            total_owed = sum(o.debt_amount for o in unpaid)
            insights.append({
                "type": "unpaid_orders",
                "severity": "info",
                "message": f"{len(unpaid)} order(s) awaiting payment — UGX {total_owed:,.0f} outstanding.",
            })
    except Exception as e:
        logger.warning("insights_unpaid_error", extra={"error": str(e)})

    # Conversations needing follow-up
    if _conv_agg_repo:
        try:
            followups = _conv_agg_repo.list_requiring_followup(org_id, limit=20)
            if followups:
                insights.append({
                    "type": "follow_up_required",
                    "severity": "info",
                    "message": f"{len(followups)} conversation(s) require follow-up today.",
                })
        except Exception as e:
            logger.warning("insights_followup_error", extra={"error": str(e)})

    # High-value customers
    try:
        top = sorted(
            _profile_repo.list(org_id, limit=200),
            key=lambda p: p.total_spend,
            reverse=True,
        )[:3]
        if top and top[0].total_spend > 0:
            names = ", ".join(f"{p.display_name} (UGX {p.total_spend:,.0f})" for p in top[:3])
            insights.append({
                "type": "top_customers",
                "severity": "positive",
                "message": f"Top customers by spend: {names}.",
            })
    except Exception as e:
        logger.warning("insights_top_customers_error", extra={"error": str(e)})

    return {"insights": insights, "count": len(insights)}


@insights_router.get("/pulse")
async def get_business_pulse(
    org_id: str,
    _token: dict = Depends(verify_firebase_token),
):
    """
    Business Pulse — homepage summary numbers.
    """
    ensure_org_access(_token, org_id)

    pulse = {
        "active_tasks": 0,
        "overdue_tasks": 0,
        "pending_orders": 0,
        "total_outstanding_ugx": 0.0,
        "customers_count": 0,
        "follow_ups_due": 0,
    }

    try:
        pulse["active_tasks"] = len(_task_repo.list_active(org_id, limit=200))
        pulse["overdue_tasks"] = len(_task_repo.list_overdue(org_id))
    except Exception:
        pass

    try:
        from domain.orders.model import OrderStatus
        pending = _order_repo.list_by_status(org_id, OrderStatus.AWAITING_PAYMENT, limit=200)
        pulse["pending_orders"] = len(pending)
        pulse["total_outstanding_ugx"] = sum(o.debt_amount for o in pending)
    except Exception:
        pass

    try:
        pulse["customers_count"] = len(_profile_repo.list(org_id, limit=500))
    except Exception:
        pass

    if _conv_agg_repo:
        try:
            pulse["follow_ups_due"] = len(_conv_agg_repo.list_requiring_followup(org_id, limit=100))
        except Exception:
            pass

    return pulse