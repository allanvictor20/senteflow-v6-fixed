"""
Tests for the language pipeline (IDEA 12) — expanded vocabulary detection,
LLM fallback path, Sunbird degradation.
"""
import pytest
from services.llm.language_pipeline import detect_language, LOCAL_LANGUAGE_HINTS


class TestKeywordDetection:
    def test_luganda_money_word(self):
        result = detect_language("nfunye ssente")
        assert result["language"] == "luganda"
        assert result["source"] == "keyword"

    def test_luganda_expanded_vocabulary(self):
        # Words added in IDEA 12 that were not in the original 6
        result = detect_language("Bambi nkuwe omubare wange")
        assert result["language"] == "luganda"
        assert result["source"] == "keyword"

    def test_swahili_business_word(self):
        result = detect_language("Tafadhali lipa deni")
        assert result["language"] == "swahili"
        assert result["source"] == "keyword"

    def test_runyankole_word(self):
        result = detect_language("nashashura esente")
        assert result["language"] == "runyankole"
        assert result["source"] == "keyword"

    def test_plain_english_defaults(self):
        result = detect_language("Brian paid 50,000 for cement")
        assert result["language"] == "english"
        assert result["source"] == "default"

    def test_empty_string_defaults_to_english(self):
        result = detect_language("")
        assert result["language"] == "english"

    def test_short_hints_not_matched(self):
        """Hints shorter than _MIN_HINT_LENGTH (3) should not trigger."""
        # 'ye', 'bo', 'mu' etc. are in the Luganda list but len < 3
        result = detect_language("ye bo")
        # Should not match these 2-char tokens
        assert result["source"] == "default"

    def test_confidence_returned(self):
        result = detect_language("ssente zange")
        assert 0.0 < result["confidence"] <= 1.0

    def test_all_languages_have_hints(self):
        required = {"luganda", "runyankole", "ateso", "luo", "swahili"}
        assert required == set(LOCAL_LANGUAGE_HINTS.keys())

    def test_luganda_has_expanded_vocabulary(self):
        # v5 expanded from 6 to 50+ — sanity check
        assert len(LOCAL_LANGUAGE_HINTS["luganda"]) >= 30
