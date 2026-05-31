"""Currency normalization for East African and common currencies."""

VALID_CURRENCIES = {"UGX", "KES", "TZS", "USD", "EUR", "GBP", "RWF", "ETB", "GHS", "NGN", "ZAR"}

CURRENCY_ALIASES = {
    "USH": "UGX",
    "UGSH": "UGX",
    "UGS": "UGX",
    "KSH": "KES",
    "KSHS": "KES",
    "TSH": "TZS",
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
}


def normalize_currency(raw: str) -> str:
    if not raw:
        return "UGX"
    upper = raw.strip().upper()
    if upper in VALID_CURRENCIES:
        return upper
    return CURRENCY_ALIASES.get(upper, "UGX")
