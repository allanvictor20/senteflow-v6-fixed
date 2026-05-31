"""
Tests for the transaction validator (deterministic layer).
Uses domain.models.Transaction (v3 model name).
"""
import pytest
from domain.models import FieldConfidence, Transaction  # shim — see domain/models.py
from validators.transaction_validator import (
    validate_transaction,
    validate_batch,
    normalize_currency,
    DuplicateDetector,
)


def make_txn(**kwargs) -> Transaction:
    defaults = dict(
        amount=50000.0,
        currency="UGX",
        transaction_type="contribution",
        category="contribution",
        description="Monthly contribution from Alice",
        date="2025-03-15",
        payer="Alice Nakato",
    )
    defaults.update(kwargs)
    return Transaction(**defaults)


class TestNormalizeCurrency:
    def test_valid_ugx(self):
        assert normalize_currency("UGX") == "UGX"

    def test_alias_ush(self):
        assert normalize_currency("USH") == "UGX"

    def test_alias_ksh(self):
        assert normalize_currency("KSH") == "KES"

    def test_unknown_defaults_to_ugx(self):
        assert normalize_currency("XYZ") == "UGX"

    def test_empty_defaults_to_ugx(self):
        assert normalize_currency("") == "UGX"

    def test_lowercase(self):
        assert normalize_currency("ugx") == "UGX"

    def test_dollar_sign(self):
        assert normalize_currency("$") == "USD"


class TestValidateTransaction:
    def test_valid_contribution(self):
        txn = make_txn()
        result = validate_transaction(txn)
        assert result.is_valid
        assert not result.has_errors

    def test_negative_amount(self):
        txn = make_txn(amount=-100)
        result = validate_transaction(txn)
        assert not result.is_valid

    def test_very_large_amount(self):
        txn = make_txn(amount=600_000_000, currency="UGX")
        result = validate_transaction(txn)
        assert not result.is_valid

    def test_future_date_warning(self):
        txn = make_txn(date="2099-01-01")
        result = validate_transaction(txn)
        warnings = [i for i in result.issues if i.severity == "warning" and i.field == "date"]
        assert len(warnings) > 0

    def test_currency_normalization(self):
        txn = make_txn(currency="USH")
        result = validate_transaction(txn)
        assert result.normalized_currency == "UGX"


class TestDuplicateDetection:
    def test_intra_batch_duplicate(self):
        txn = make_txn()
        detector = DuplicateDetector()
        r1 = validate_transaction(txn, detector)
        assert not r1.is_duplicate
        r2 = validate_transaction(txn, detector)
        assert r2.is_duplicate

    def test_different_transactions_not_duplicate(self):
        txn1 = make_txn(payer="Alice")
        txn2 = make_txn(payer="Bob")
        detector = DuplicateDetector()
        r1 = validate_transaction(txn1, detector)
        r2 = validate_transaction(txn2, detector)
        assert not r1.is_duplicate
        assert not r2.is_duplicate


class TestValidateBatch:
    def test_batch_deduplication(self):
        txns = [make_txn(), make_txn()]
        results = validate_batch(txns)
        dup_count = sum(1 for _, r in results if r.is_duplicate)
        assert dup_count == 1
