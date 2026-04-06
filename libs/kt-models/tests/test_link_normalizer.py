"""Tests for kt_models.link_normalizer."""

from __future__ import annotations

from kt_models.link_normalizer import normalize_ai_links

# ── Double-brace fact tokens ────────────────────────────────────────


def test_double_brace_fact_token() -> None:
    text = "Evidence shows {{fact:a1b2c3d4-e5f6-7890-abcd-ef1234567890|NASA confirmed water}} that water exists."
    result = normalize_ai_links(text)
    assert (
        result
        == "Evidence shows [NASA confirmed water](/facts/a1b2c3d4-e5f6-7890-abcd-ef1234567890) that water exists."
    )


# ── Single-brace fact tokens with label ─────────────────────────────


def test_single_brace_fact_with_label() -> None:
    text = "The data {fact:a1b2c3d4-e5f6-7890-abcd-ef1234567890|shows rapid growth} is clear."
    result = normalize_ai_links(text)
    assert result == "The data [shows rapid growth](/facts/a1b2c3d4-e5f6-7890-abcd-ef1234567890) is clear."


# ── Bare fact tokens ────────────────────────────────────────────────


def test_bare_fact_token_converted() -> None:
    text = "Supported by {fact:a1b2c3d4-e5f6-7890-abcd-ef1234567890}."
    result = normalize_ai_links(text)
    assert result == "Supported by [source](/facts/a1b2c3d4-e5f6-7890-abcd-ef1234567890)."


def test_bare_fact_token_preserved() -> None:
    text = "Justified by {fact:a1b2c3d4-e5f6-7890-abcd-ef1234567890}."
    result = normalize_ai_links(text, preserve_fact_tokens=True)
    assert result == "Justified by {fact:a1b2c3d4-e5f6-7890-abcd-ef1234567890}."


# ── Colon instead of slash ──────────────────────────────────────────


def test_facts_colon_with_paren() -> None:
    text = "[water ice](/facts:a1b2c3d4-e5f6-7890-abcd-ef1234567890)"
    assert normalize_ai_links(text) == "[water ice](/facts/a1b2c3d4-e5f6-7890-abcd-ef1234567890)"


def test_nodes_colon_with_paren() -> None:
    text = "[Moon](/nodes:a1b2c3d4-e5f6-7890-abcd-ef1234567890)"
    assert normalize_ai_links(text) == "[Moon](/nodes/a1b2c3d4-e5f6-7890-abcd-ef1234567890)"


def test_colon_with_wrong_bracket() -> None:
    text = "[label](/facts:a1b2c3d4-e5f6-7890-abcd-ef1234567890]"
    assert normalize_ai_links(text) == "[label](/facts/a1b2c3d4-e5f6-7890-abcd-ef1234567890)"


# ── Wrong closing bracket ───────────────────────────────────────────


def test_facts_wrong_bracket() -> None:
    text = "[water ice](/facts/a1b2c3d4-e5f6-7890-abcd-ef1234567890]"
    assert normalize_ai_links(text) == "[water ice](/facts/a1b2c3d4-e5f6-7890-abcd-ef1234567890)"


def test_nodes_wrong_bracket() -> None:
    text = "[Moon](/nodes/a1b2c3d4-e5f6-7890-abcd-ef1234567890]"
    assert normalize_ai_links(text) == "[Moon](/nodes/a1b2c3d4-e5f6-7890-abcd-ef1234567890)"


# ── Bare bracket (no link text) ─────────────────────────────────────


def test_bare_facts_bracket() -> None:
    text = "See [/facts/a1b2c3d4-e5f6-7890-abcd-ef1234567890] for details."
    assert normalize_ai_links(text) == "See [source](/facts/a1b2c3d4-e5f6-7890-abcd-ef1234567890) for details."


def test_bare_nodes_bracket() -> None:
    text = "See [/nodes/a1b2c3d4-e5f6-7890-abcd-ef1234567890] for details."
    assert normalize_ai_links(text) == "See [node](/nodes/a1b2c3d4-e5f6-7890-abcd-ef1234567890) for details."


def test_bare_bracket_with_colon() -> None:
    text = "[/facts:a1b2c3d4-e5f6-7890-abcd-ef1234567890]"
    assert normalize_ai_links(text) == "[source](/facts/a1b2c3d4-e5f6-7890-abcd-ef1234567890)"


# ── Valid links pass through unchanged ──────────────────────────────


def test_valid_fact_link_unchanged() -> None:
    text = "[water ice](/facts/a1b2c3d4-e5f6-7890-abcd-ef1234567890)"
    assert normalize_ai_links(text) == text


def test_valid_node_link_unchanged() -> None:
    text = "[Moon Formation](/nodes/a1b2c3d4-e5f6-7890-abcd-ef1234567890)"
    assert normalize_ai_links(text) == text


def test_external_link_unchanged() -> None:
    text = "[NASA](https://nasa.gov)"
    assert normalize_ai_links(text) == text


# ── Mixed content ───────────────────────────────────────────────────


def test_mixed_content() -> None:
    text = (
        "According to [NASA](https://nasa.gov), "
        "[water ice](/facts/aaaa-bbbb-cccc-dddd-eeee) was found. "
        "See also {fact:1111-2222-3333-4444-5555|rapid growth} and "
        "[Moon](/nodes:6666-7777-8888-9999-0000]."
    )
    result = normalize_ai_links(text)
    assert "[NASA](https://nasa.gov)" in result
    assert "[water ice](/facts/aaaa-bbbb-cccc-dddd-eeee)" in result
    assert "[rapid growth](/facts/1111-2222-3333-4444-5555)" in result
    assert "[Moon](/nodes/6666-7777-8888-9999-0000)" in result


# ── Edge cases ──────────────────────────────────────────────────────


def test_empty_string() -> None:
    assert normalize_ai_links("") == ""


def test_none_like_empty() -> None:
    assert normalize_ai_links("") == ""


def test_no_links() -> None:
    text = "Plain text with no links at all."
    assert normalize_ai_links(text) == text


def test_multiple_malformed_in_one_text() -> None:
    uid1 = "aaaa-bbbb-cccc-dddd-eeee0001"
    uid2 = "aaaa-bbbb-cccc-dddd-eeee0002"
    uid3 = "aaaa-bbbb-cccc-dddd-eeee0003"
    text = f"{{fact:{uid1}|label1}} and {{fact:{uid2}|label2}} plus [x](/facts:{uid3}]"
    result = normalize_ai_links(text)
    assert f"[label1](/facts/{uid1})" in result
    assert f"[label2](/facts/{uid2})" in result
    assert f"[x](/facts/{uid3})" in result
