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
    assert len(sentences) >= 2  # heading stripped, sentences preserved


def test_split_into_sentences_empty():
    assert split_into_sentences("") == []
    assert split_into_sentences("   ") == []


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
    # "gravity" appears in sentences 0 and 2
    assert 0 in links
    assert any(nid == "uuid-1" for nid, _ in links[0])
    assert 2 in links
    assert any(nid == "uuid-1" for nid, _ in links[2])
    # "Newton" appears in sentence 1
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
