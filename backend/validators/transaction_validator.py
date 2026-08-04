"""
SenteFlow AI — Transaction Validator
======================================
Deterministic validation layer for AI-extracted transactions.

The AI layer identifies facts; this layer enforces rules. It runs between
extraction and persistence so obviously-wrong records (negative amounts,
impossible dates, exact duplicates) never reach Firestore or the review UI.

Nothing here calls an LLM — every check is reproducible.
"""

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from typing import Iterable, Optional

from domain.models import Transaction
from utils.clock import utc_now

logger = logging.getLogger(__name__)

# ── Currency normalisation ────────────────────────────────────────────────────

DEFAULT_CURRENCY = "UGX"

SUPPORTED_CURRENCIES = {"UGX", "KES", "TZS", "RWF", "USD", "EUR", "GBP"}

_CURRENCY_ALIASES = {
    "USH": "UGX", "USHS": "UGX", "SHS": "UGX", "UG": "UGX", "UGSH": "UGX",
    "KSH": "KES", "KSHS": "KES", "KES/=": "KES",
    "TSH": "TZS", "TSHS": "TZS",
    "RWF/=": "RWF", "FRW": "RWF",
    "$": "USD", "US$": "USD", "USD$": "USD", "DOLLAR": "USD", "DOLLARS": "USD",
    "€": "EUR", "EURO": "EUR", "EUROS": "EUR",
    "£": "GBP", "POUND": "GBP", "POUNDS": "GBP",
}

# Per-currency sanity ceilings for a single SME transaction.
_MAX_AMOUNT = {
    "UGX": 500_000_000,
    "KES": 15_000_000,
    "TZS": 300_000_000,
    "RWF": 150_000_000,
    "USD": 150_000,
    "EUR": 150_000,
    "GBP": 150_000,
}

_MIN_AMOUNT = 1.0

_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d %b %Y", "%d %B %Y")

# How far in the past a date can be before we flag it as suspicious.
_MAX_AGE_DAYS = 365 * 3


def normalize_currency(raw: Optional[str]) -> str:
    """
    Map a free-form currency string onto a supported ISO code.

    Unrecognised input falls back to UGX — the home currency — rather than
    failing the whole transaction, but the caller gets a warning issue so the
    guess is visible in the review UI.
    """
    if not raw:
        return DEFAULT_CURRENCY
    cleaned = str(raw).strip().upper().replace(".", "")
    if cleaned in SUPPORTED_CURRENCIES:
        return cleaned
    if cleaned in _CURRENCY_ALIASES:
        return _CURRENCY_ALIASES[cleaned]
    return DEFAULT_CURRENCY


# ── Issues and results ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ValidationIssue:
    field: str
    severity: str           # "error" blocks saving; "warning" is advisory
    message: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.field}: {self.message}"


@dataclass
class ValidationResult:
    transaction: Optional[Transaction] = None
    issues: list[ValidationIssue] = field(default_factory=list)
    normalized_currency: str = DEFAULT_CURRENCY
    is_duplicate: bool = False
    fingerprint: str = ""

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)

    @property
    def is_valid(self) -> bool:
        return not self.has_errors and not self.is_duplicate


# ── Duplicate detection ───────────────────────────────────────────────────────

def transaction_fingerprint(txn: Transaction) -> str:
    """Stable hash of the fields that make a transaction unique."""
    parts = [
        f"{float(txn.amount or 0):.2f}",
        normalize_currency(txn.currency),
        (txn.payer or txn.member_name or "").strip().lower(),
        (txn.payee or "").strip().lower(),
        str(txn.date or "")[:10],
        (txn.description or "").strip().lower()[:80],
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


class DuplicateDetector:
    """
    Tracks fingerprints seen so far.

    Seed it with `known` to catch cross-session duplicates; leave it empty to
    de-duplicate only within the current batch.
    """

    def __init__(self, known: Optional[Iterable[str]] = None):
        self._seen: set[str] = set(known or ())

    def check(self, fingerprint: str) -> bool:
        """Return True if this fingerprint was already seen, then record it."""
        if fingerprint in self._seen:
            return True
        self._seen.add(fingerprint)
        return False

    def __contains__(self, fingerprint: str) -> bool:
        return fingerprint in self._seen

    def __len__(self) -> int:
        return len(self._seen)


# ── Field checks ──────────────────────────────────────────────────────────────

def _parse_date(raw: str) -> Optional[datetime]:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw.strip()[:len(raw.strip())], fmt)
        except (ValueError, TypeError):
            continue
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


def _check_amount(txn: Transaction, currency: str, issues: list[ValidationIssue]) -> None:
    try:
        amount = float(txn.amount)
    except (TypeError, ValueError):
        issues.append(ValidationIssue("amount", "error", "Amount is not a number."))
        return

    if amount <= 0:
        issues.append(ValidationIssue("amount", "error", "Amount must be greater than zero."))
        return
    if amount < _MIN_AMOUNT:
        issues.append(ValidationIssue("amount", "warning", f"Amount {amount} is implausibly small."))

    ceiling = _MAX_AMOUNT.get(currency, _MAX_AMOUNT[DEFAULT_CURRENCY])
    if amount > ceiling:
        issues.append(ValidationIssue(
            "amount", "error",
            f"Amount {amount:,.0f} {currency} exceeds the {ceiling:,.0f} limit for a single transaction.",
        ))


def _check_date(txn: Transaction, issues: list[ValidationIssue]) -> None:
    if not txn.date:
        issues.append(ValidationIssue("date", "warning", "No date recorded."))
        return

    parsed = _parse_date(str(txn.date))
    if parsed is None:
        issues.append(ValidationIssue("date", "warning", f"Could not parse date '{txn.date}'."))
        return

    now = utc_now()
    if parsed > now + timedelta(days=1):
        issues.append(ValidationIssue("date", "warning", f"Date {txn.date} is in the future."))
    elif parsed < now - timedelta(days=_MAX_AGE_DAYS):
        issues.append(ValidationIssue("date", "warning", f"Date {txn.date} is more than 3 years old."))


def _check_parties(txn: Transaction, issues: list[ValidationIssue]) -> None:
    if not (txn.payer or txn.payee or txn.member_name):
        issues.append(ValidationIssue("payer", "warning", "No payer or payee identified."))


def _check_description(txn: Transaction, issues: list[ValidationIssue]) -> None:
    if not (txn.description or "").strip():
        issues.append(ValidationIssue("description", "warning", "No description recorded."))


# ── Public API ────────────────────────────────────────────────────────────────

def validate_transaction(
    txn: Transaction,
    detector: Optional[DuplicateDetector] = None,
) -> ValidationResult:
    """
    Validate and normalise a single transaction.

    The returned result carries a *copy* of the transaction with its currency
    normalised, so callers can persist `result.transaction` directly.
    """
    issues: list[ValidationIssue] = []

    raw_currency = txn.currency
    currency = normalize_currency(raw_currency)
    if raw_currency and currency != str(raw_currency).strip().upper():
        issues.append(ValidationIssue(
            "currency", "warning",
            f"Currency '{raw_currency}' normalised to {currency}.",
        ))

    _check_amount(txn, currency, issues)
    _check_date(txn, issues)
    _check_parties(txn, issues)
    _check_description(txn, issues)

    normalized = txn.model_copy(update={"currency": currency})
    fingerprint = transaction_fingerprint(normalized)

    is_duplicate = detector.check(fingerprint) if detector is not None else False
    if is_duplicate:
        issues.append(ValidationIssue(
            "duplicate", "warning",
            "An identical transaction has already been recorded.",
        ))

    return ValidationResult(
        transaction=normalized,
        issues=issues,
        normalized_currency=currency,
        is_duplicate=is_duplicate,
        fingerprint=fingerprint,
    )


def validate_batch(
    transactions: Iterable[Transaction],
    known_fingerprints: Optional[Iterable[str]] = None,
) -> list[tuple[Transaction, ValidationResult]]:
    """
    Validate a batch, de-duplicating within it and against `known_fingerprints`.

    Returns (original_transaction, result) pairs in input order.
    """
    detector = DuplicateDetector(known_fingerprints)
    results = [(txn, validate_transaction(txn, detector)) for txn in transactions]

    invalid = sum(1 for _, r in results if r.has_errors)
    duplicates = sum(1 for _, r in results if r.is_duplicate)
    if invalid or duplicates:
        logger.info(
            "batch_validated",
            extra={"total": len(results), "invalid": invalid, "duplicates": duplicates},
        )
    return results
