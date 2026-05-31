"""
Tests for the permission system (IDEA 09).
No Firestore needed — pure domain logic.
"""
import pytest
from domain.permissions.model import (
    OrgConfig,
    ActionPermission,
    PermissionMode,
)


class TestActionPermission:
    def test_auto_never_requires_approval(self):
        perm = ActionPermission("update_customer_profile", mode=PermissionMode.AUTO)
        assert perm.requires_approval() is False
        assert perm.requires_approval(amount=1_000_000) is False

    def test_approval_required_always_blocks(self):
        perm = ActionPermission("reduce_debt", mode=PermissionMode.APPROVAL_REQUIRED)
        assert perm.requires_approval() is True
        assert perm.requires_approval(amount=0) is True

    def test_threshold_allows_below_limit(self):
        perm = ActionPermission("update_ledger", mode=PermissionMode.THRESHOLD, threshold=500_000)
        assert perm.requires_approval(amount=499_999) is False
        assert perm.requires_approval(amount=None) is False

    def test_threshold_blocks_above_limit(self):
        perm = ActionPermission("update_ledger", mode=PermissionMode.THRESHOLD, threshold=500_000)
        assert perm.requires_approval(amount=500_001) is True
        assert perm.requires_approval(amount=1_000_000) is True

    def test_threshold_edge_case_exact_value(self):
        perm = ActionPermission("update_ledger", mode=PermissionMode.THRESHOLD, threshold=500_000)
        # Exactly at threshold: NOT blocked (must be strictly greater)
        assert perm.requires_approval(amount=500_000) is False

    def test_custom_approval_question_returned(self):
        perm = ActionPermission(
            "reduce_debt",
            mode=PermissionMode.APPROVAL_REQUIRED,
            approval_question="Clear this debt? Reply YES.",
        )
        assert perm.get_approval_question() == "Clear this debt? Reply YES."

    def test_default_approval_question_includes_summary(self):
        perm = ActionPermission("reduce_debt", mode=PermissionMode.APPROVAL_REQUIRED)
        q = perm.get_approval_question("Brian owes 50k")
        assert "Brian owes 50k" in q


class TestOrgConfig:
    def test_default_config_reduce_debt_requires_approval(self):
        config = OrgConfig.default("org-1")
        perm = config.get_permission("reduce_debt")
        assert perm.requires_approval() is True

    def test_default_config_update_ledger_threshold(self):
        config = OrgConfig.default("org-1")
        perm = config.get_permission("update_ledger")
        assert perm.requires_approval(amount=499_000) is False
        assert perm.requires_approval(amount=600_000) is True

    def test_default_config_profile_update_is_auto(self):
        config = OrgConfig.default("org-1")
        perm = config.get_permission("update_customer_profile")
        assert perm.requires_approval() is False

    def test_unknown_action_defaults_to_auto(self):
        config = OrgConfig.default("org-1")
        perm = config.get_permission("some_future_action")
        assert perm.requires_approval() is False

    def test_org_specific_override_takes_precedence(self):
        custom_perm = ActionPermission("update_ledger", mode=PermissionMode.AUTO)
        config = OrgConfig(org_id="org-1", permissions={"update_ledger": custom_perm})
        perm = config.get_permission("update_ledger")
        # Custom org config overrides default threshold
        assert perm.requires_approval(amount=1_000_000) is False
