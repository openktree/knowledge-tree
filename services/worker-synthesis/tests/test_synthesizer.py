"""Tests for the synthesizer agent and document processing pipeline."""

from __future__ import annotations

from kt_worker_synthesis.pipelines.document_processing import (
    link_nodes_by_text,
    split_into_sentences,
)


def test_split_into_sentences_basic():
    text = "This is the first sentence. This is the second sentence. And a third one."
    sentences = split_into_sentences(text)
    assert len(sentences) == 3
    assert "first" in sentences[0]
    assert "second" in sentences[1]


def test_split_into_sentences_markdown():
    text = "## Introduction\n\nThe topic is complex. It has many facets. Each deserves attention."
    sentences = split_into_sentences(text)
    assert any("Introduction" in s for s in sentences)
    assert any("complex" in s for s in sentences)


def test_split_into_sentences_empty():
    assert split_into_sentences("") == []
    assert split_into_sentences("   ") == []


def test_split_into_sentences_list_items():
    """Each list item should become its own sentence."""
    text = """Key points:

1. **First item** is about alpha.
2. **Second item** is about beta.
3. **Third item** is about gamma.
"""
    sentences = split_into_sentences(text)
    # "Key points:" becomes one sentence, each list item becomes its own
    list_sentences = [s for s in sentences if "item" in s]
    assert len(list_sentences) == 3
    assert any("alpha" in s for s in list_sentences)
    assert any("beta" in s for s in list_sentences)
    assert any("gamma" in s for s in list_sentences)


def test_split_into_sentences_bullet_list():
    """Bullet lists should also split per item."""
    text = """Overview:

- Apples are red.
- Bananas are yellow.
- Grapes are purple.
"""
    sentences = split_into_sentences(text)
    fruit_sentences = [s for s in sentences if "are" in s]
    assert len(fruit_sentences) == 3


def test_split_into_sentences_mixed():
    """Mix of paragraphs, headings, and lists."""
    text = """## The Topic

This is an introduction. It has two sentences.

Key developments:

1. First development happened.
2. Second development followed.

This is the conclusion.
"""
    sentences = split_into_sentences(text)
    assert any("Topic" in s for s in sentences)
    assert any("introduction" in s for s in sentences)
    assert any("First development" in s for s in sentences)
    assert any("Second development" in s for s in sentences)
    assert any("conclusion" in s for s in sentences)


def test_link_nodes_by_text_basic():
    sentences = [
        "The concept of gravity is fundamental.",
        "Newton proposed the theory.",
        "Gravity affects everything.",
    ]
    node_names = {
        "uuid-1": ["gravity"],
        "uuid-2": ["Newton"],
    }
    links = link_nodes_by_text(sentences, node_names)
    assert 0 in links
    assert any(nid == "uuid-1" for nid, _ in links[0])
    assert 2 in links
    assert any(nid == "uuid-1" for nid, _ in links[2])
    assert 1 in links
    assert any(nid == "uuid-2" for nid, _ in links[1])


def test_link_nodes_by_text_markdown_links():
    sentences = [
        "According to [Albert Einstein](/nodes/abc-123), relativity changed physics.",
    ]
    node_names: dict[str, list[str]] = {}
    links = link_nodes_by_text(sentences, node_names)
    assert 0 in links
    assert any(nid == "abc-123" for nid, _ in links[0])


def test_link_nodes_by_text_case_insensitive():
    sentences = ["GRAVITY is a force."]
    node_names = {"uuid-1": ["gravity"]}
    links = link_nodes_by_text(sentences, node_names)
    assert 0 in links
