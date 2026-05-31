"""
SenteFlow AI — Activity Validator
====================================
Validates BusinessActivity objects.
transaction_validator.py is left alone — it still serves the
legacy extraction workflow.
"""

import logging
from domain.events.business_event import BusinessEvent as BusinessActivity
DebtActivity = BusinessActivity
PaymentActivity = BusinessActivity
ReminderActivity = BusinessActivity

logger = logging.getLogger(__name__)

VALID_CURRENCIES = {"UGX", "KES", "TZS", "USD", "EUR", "GBP"}
AMOUNT_MIN = 100
AMOUNT_MAX = 500_000_000


def validate_activity(activity: BusinessActivity) -> dict:
    issues = []
    warnings = []

    if activity.amount is not None:
        if activity.amount <= 0:
            issues.append("Amount must be greater than zero.")
        if activity.amount < AMOUNT_MIN:
            warnings.append(f"Amount {activity.amount} is unusually small.")
        if activity.amount > AMOUNT_MAX:
            warnings.append(f"Amount {activity.amount} is unusually large — please verify.")

    if activity.currency not in VALID_CURRENCIES:
        warnings.append(f"Unrecognized currency '{activity.currency}', defaulting to UGX.")

    if isinstance(activity, DebtActivity):
        if not activity.debtor:
            issues.append("Debt activity must have a debtor name.")

    if isinstance(activity, PaymentActivity):
        if not activity.payer and not activity.payee:
            warnings.append("Payment activity has no payer or payee.")

    if isinstance(activity, ReminderActivity):
        if not activity.due_date and not activity.target_person:
            warnings.append("Reminder has no due date and no target person.")

    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "warnings": warnings,
    }