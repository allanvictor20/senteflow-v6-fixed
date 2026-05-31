"""
SenteFlow — Permission System (IDEA 09)
=======================================
Controls which actions fire automatically vs. require owner approval.

Owner trust is more important than speed.
An agent that clears a debt without confirmation destroys trust.

PermissionMode:
  AUTO             — fires immediately (low-risk, e.g. update_customer_profile)
  APPROVAL_REQUIRED — sends owner a confirmation question first
  THRESHOLD        — auto below threshold, approval above it

OrgConfig holds per-org permission settings.
If an org has no config, OrgConfig.default() applies sensible safe defaults.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class PermissionMode(str, Enum):
    AUTO = "auto"
    APPROVAL_REQUIRED = "approval_required"
    THRESHOLD = "threshold"


@dataclass
class ActionPermission:
    action_key: str
    mode: PermissionMode = PermissionMode.AUTO
    threshold: Optional[float] = None        # UGX amount — only used with THRESHOLD mode
    approval_question: Optional[str] = None  # WhatsApp question sent to owner

    def requires_approval(self, amount: Optional[float] = None) -> bool:
        if self.mode == PermissionMode.APPROVAL_REQUIRED:
            return True
        if self.mode == PermissionMode.THRESHOLD and self.threshold is not None:
            if amount is not None and amount > self.threshold:
                return True
        return False

    def get_approval_question(self, event_summary: str = "") -> str:
        if self.approval_question:
            return self.approval_question
        return f"Should I proceed with: {event_summary}? Reply YES to confirm or NO to cancel."


# Default permission settings applied to all orgs unless overridden
_DEFAULT_PERMISSIONS: dict[str, ActionPermission] = {
    # High-risk: always ask before firing
    "reduce_debt": ActionPermission(
        "reduce_debt",
        mode=PermissionMode.APPROVAL_REQUIRED,
        approval_question=(
            "Should I clear this debt? "
            "Reply YES to confirm the payment was received, or NO to keep it outstanding."
        ),
    ),
    # High-value threshold: auto up to 500k, ask above
    "update_ledger": ActionPermission(
        "update_ledger",
        mode=PermissionMode.THRESHOLD,
        threshold=500_000,
        approval_question=(
            "This is a large transaction (over UGX 500,000). "
            "Should I record it? Reply YES to confirm."
        ),
    ),
    # Medium-risk: ask before sending reminders to customers
    "schedule_reminder": ActionPermission(
        "schedule_reminder",
        mode=PermissionMode.AUTO,  # Auto by default — reminder is stored, not sent immediately
    ),
    # Notifications to owner: always auto
    "notify_owner": ActionPermission("notify_owner", mode=PermissionMode.AUTO),
    # Low-risk profile + conversation updates: always auto
    "update_customer_profile": ActionPermission("update_customer_profile", mode=PermissionMode.AUTO),
    "update_conversation": ActionPermission("update_conversation", mode=PermissionMode.AUTO),
    "create_order": ActionPermission("create_order", mode=PermissionMode.AUTO),
    "create_alert": ActionPermission("create_alert", mode=PermissionMode.AUTO),
    "update_inventory": ActionPermission("update_inventory", mode=PermissionMode.AUTO),
    "run_extraction_workflow": ActionPermission("run_extraction_workflow", mode=PermissionMode.AUTO),
}


@dataclass
class OrgConfig:
    org_id: str
    permissions: dict[str, ActionPermission] = field(default_factory=dict)

    def get_permission(self, action_key: str) -> ActionPermission:
        """Return org-specific permission, falling back to defaults."""
        return (
            self.permissions.get(action_key)
            or _DEFAULT_PERMISSIONS.get(action_key)
            or ActionPermission(action_key, mode=PermissionMode.AUTO)
        )

    @classmethod
    def default(cls, org_id: str = "default") -> "OrgConfig":
        return cls(org_id=org_id, permissions={})
