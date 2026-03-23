from kt_facts.processing.segmenter import chunk_if_needed, segment_text

# ── chunk_if_needed tests ─────────────────────────────────────────


def test_chunk_small_text_returns_single():
    """Text under threshold returns as single chunk."""
    text = "This is a short piece of text about water properties."
    chunks = chunk_if_needed(text)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_chunk_empty_text():
    chunks = chunk_if_needed("")
    assert chunks == []


def test_chunk_whitespace_only():
    chunks = chunk_if_needed("   \n\n   \n   ")
    assert chunks == []


def test_chunk_large_text_splits():
    """Text exceeding max_chunk gets split."""
    # Create text larger than threshold
    paragraphs = [f"Paragraph {i}. " + "X" * 100 for i in range(10)]
    text = "\n\n".join(paragraphs)
    chunks = chunk_if_needed(text, max_chunk=500)
    assert len(chunks) > 1
    # All content preserved
    joined = "\n\n".join(chunks)
    assert "Paragraph 0" in joined
    assert "Paragraph 9" in joined


def test_chunk_preserves_content():
    text = "First paragraph about science.\n\nSecond paragraph about math."
    chunks = chunk_if_needed(text)
    assert len(chunks) == 1  # Under 50k chars, stays as one chunk
    assert "First paragraph" in chunks[0]
    assert "Second paragraph" in chunks[0]


def test_chunk_web_snippet_stays_single():
    """Typical web search snippets (200-400 chars) always stay as one chunk."""
    snippet = "Water is a chemical substance with the formula H2O. It covers 71% of Earth's surface."
    chunks = chunk_if_needed(snippet)
    assert len(chunks) == 1
    assert chunks[0] == snippet


# ── Legacy segment_text tests (backwards compatibility) ──────────


def test_segment_paragraphs():
    text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    segments = segment_text(text, max_segment_length=200)
    # With the new chunking behavior at small threshold, these get merged
    assert len(segments) >= 1
    joined = "\n\n".join(segments)
    assert "First paragraph." in joined
    assert "Third paragraph." in joined


def test_segment_long_paragraph():
    # A very long paragraph should be split
    text = ". ".join(["Sentence " + str(i) for i in range(50)])
    segments = segment_text(text, max_segment_length=200)
    assert len(segments) > 1


def test_segment_empty_text():
    segments = segment_text("")
    assert segments == []


def test_segment_whitespace_only():
    segments = segment_text("   \n\n   \n   ")
    assert segments == []


def test_segment_preserves_content():
    text = "This is a test paragraph.\n\nAnother paragraph here."
    segments = segment_text(text, max_segment_length=200)
    joined = "\n\n".join(segments)
    assert "This is a test paragraph." in joined
    assert "Another paragraph here." in joined


def test_segment_single_paragraph():
    text = "Just one paragraph with no double newlines."
    segments = segment_text(text)
    assert len(segments) == 1
    assert segments[0] == "Just one paragraph with no double newlines."


def test_segment_filters_empty_paragraphs():
    text = "First paragraph.\n\n\n\n\n\nSecond paragraph."
    segments = segment_text(text, max_segment_length=200)
    joined = "\n\n".join(segments)
    assert "First paragraph." in joined
    assert "Second paragraph." in joined
