"""
Tests for EventExtractor — amount parsing, entity coercion, extraction pipeline.
No API key needed: _call_gemini is mocked throughout.
"""
import pytest
from unittest.mock import AsyncMock, patch
from domain.events.event_types import EventType
from services.llm.event_extractor import _parse_amount, _coerce_entities, extract_event


class TestParseAmount:
    def test_integer(self):                assert _parse_amount(50000) == 50000.0
    def test_float(self):                  assert _parse_amount(50.5) == 50.5
    def test_plain_string(self):           assert _parse_amount("50000") == 50000.0
    def test_k_lower(self):                assert _parse_amount("50k") == 50000.0
    def test_k_upper(self):                assert _parse_amount("50K") == 50000.0
    def test_k_decimal(self):              assert _parse_amount("50.5k") == 50500.0
    def test_ugx_prefix(self):             assert _parse_amount("UGX 50,000") == 50000.0
    def test_comma_separator(self):        assert _parse_amount("50,000") == 50000.0
    def test_shs_prefix(self):             assert _parse_amount("Shs 1,500") == 1500.0
    def test_none_returns_none(self):      assert _parse_amount(None) is None
    def test_empty_returns_none(self):     assert _parse_amount("") is None
    def test_zero(self):                   assert _parse_amount(0) == 0.0
    def test_unparseable(self):            assert _parse_amount("abc") is None


class TestCoerceEntities:
    def test_amount_string_to_float(self):
        assert _coerce_entities({"amount": "50k"})["amount"] == 50000.0

    def test_non_dict_returns_empty(self):
        assert _coerce_entities(None) == {}
        assert _coerce_entities([1, 2]) == {}

    def test_unknown_keys_pass_through(self):
        r = _coerce_entities({"item": "cement"})
        assert r["item"] == "cement"

    def test_quantity_coerced(self):
        assert _coerce_entities({"quantity": "3"})["quantity"] == 3.0

    def test_unparseable_amount_kept(self):
        assert _coerce_entities({"amount": "unknown"})["amount"] == "unknown"

    def test_empty_dict(self):
        assert _coerce_entities({}) == {}


VALID_RESPONSE = {
    "event_type": "payment_received",
    "confidence": 0.95,
    "entities": {"amount": 50000.0, "payer": "Sarah", "currency": "UGX"},
    "reasoning": "clear payment",
    "recommended_actions": ["update_ledger"],
    "operational_effects": [],
}


@pytest.mark.asyncio
async def test_extract_event_happy_path():
    with patch("services.ai.event_extractor._call_gemini", new=AsyncMock(return_value=VALID_RESPONSE)):
        event = await extract_event("Sarah paid 50k", "256700000001@c.us", "org-1")
    assert event.event_type == EventType.PAYMENT_RECEIVED
    assert event.confidence == 0.95
    assert event.entities["payer"] == "Sarah"


@pytest.mark.asyncio
async def test_unknown_event_type_fallback():
    bad = {**VALID_RESPONSE, "event_type": "invented_type"}
    with patch("services.ai.event_extractor._call_gemini", new=AsyncMock(return_value=bad)):
        event = await extract_event("some msg", "s-1", "org-1")
    assert event.event_type == EventType.UNKNOWN


@pytest.mark.asyncio
async def test_confidence_clamped():
    over = {**VALID_RESPONSE, "confidence": 1.8}
    with patch("services.ai.event_extractor._call_gemini", new=AsyncMock(return_value=over)):
        event = await extract_event("msg", "s-1", "org-1")
    assert event.confidence <= 1.0


@pytest.mark.asyncio
async def test_gemini_failure_returns_unknown():
    with patch("services.ai.event_extractor._call_gemini", new=AsyncMock(side_effect=Exception("timeout"))):
        event = await extract_event("Sarah paid 50k", "s-1", "org-1")
    assert event.event_type == EventType.UNKNOWN
    assert event.confidence == 0.0
    assert "Extraction failed" in event.reasoning