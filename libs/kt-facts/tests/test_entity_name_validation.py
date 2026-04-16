"""Unit tests for ``is_valid_entity_name``."""

from __future__ import annotations

from kt_core_engine_api.extractor import is_valid_entity_name


class TestIsValidEntityName:
    def test_valid_names(self) -> None:
        assert is_valid_entity_name("Albert Einstein") is True
        assert is_valid_entity_name("NASA") is True
        assert is_valid_entity_name("CRISPR-Cas9") is True
        assert is_valid_entity_name("World Health Organization") is True
        assert is_valid_entity_name("quantum entanglement") is True
        assert is_valid_entity_name("FBI") is True
        assert is_valid_entity_name("2008 financial crisis") is True
        assert is_valid_entity_name("Jennifer Doudna") is True
        assert is_valid_entity_name("Ab") is True

    def test_reject_too_short(self) -> None:
        assert is_valid_entity_name("") is False
        assert is_valid_entity_name("A") is False

    def test_reject_too_long(self) -> None:
        assert is_valid_entity_name("x" * 151) is False

    def test_reject_pure_initials(self) -> None:
        assert is_valid_entity_name("K. M. A.") is False
        assert is_valid_entity_name("J. R. R.") is False
        assert is_valid_entity_name("A B C D") is False
        assert is_valid_entity_name("K M A M H") is False

    def test_reject_et_al(self) -> None:
        assert is_valid_entity_name("Smith et al.") is False
        assert is_valid_entity_name("K.M.A. et al.") is False
        assert is_valid_entity_name("Silva et al. (2020)") is False

    def test_reject_low_alpha_ratio(self) -> None:
        assert is_valid_entity_name("... --- ...") is False
        assert is_valid_entity_name("1234567890") is False
        assert is_valid_entity_name("(((((())))))") is False

    def test_reject_repeated_patterns(self) -> None:
        assert is_valid_entity_name("K. M. A. K. M. A. K. M. A.") is False
        assert is_valid_entity_name("abc abc abc abc abc") is False

    def test_accept_legitimate_repeated_words(self) -> None:
        assert is_valid_entity_name("New York City") is True
        assert is_valid_entity_name("World Wide Web") is True

    def test_accept_known_acronyms(self) -> None:
        assert is_valid_entity_name("FBI") is True
        assert is_valid_entity_name("WHO") is True
        assert is_valid_entity_name("UNESCO") is True
