import pytest
from services.llm.event_extractor import _parse_amount


@pytest.mark.parametrize("raw,expected", [
    (0,          0.0),
    (500,        500.0),
    (50000,      50000.0),
    (1500.50,    1500.50),
    ("500",      500.0),
    ("50000",    50000.0),
    ("1500.50",  1500.50),
    ("50k",      50000.0),
    ("50K",      50000.0),
    ("500k",     500000.0),
    ("2.5k",     2500.0),
    ("50.5k",    50500.0),
    ("UGX 50,000", 50000.0),
    ("Shs 1,500",  1500.0),
    ("KES 200",    200.0),
    ("USD 100",    100.0),
    ("50,000",     50000.0),
    ("1,500,000",  1500000.0),
    (None,   None),
    ("",     None),
    ("unknown", None),
    ("N/A",     None),
])
def test_parse_amount(raw, expected):
    assert _parse_amount(raw) == expected