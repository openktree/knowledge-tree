"""Tests for author → entity suggestion merge in gathering pipeline."""

from __future__ import annotations

import uuid

import pytest

from kt_facts.author import AuthorInfo


# Import from the gathering pipeline
from kt_worker_nodes.pipelines.gathering.pipeline import (
    authors_to_entity_suggestions,
    merge_extracted_nodes,
)


class _FakeFact:
    """Minimal Fact-like object for testing."""

    def __init__(self) -> None:
        self.id = uuid.uuid4()
        self.content = "test fact"
        self.fact_type = "claim"


class TestAuthorsToEntitySuggestions:
    def test_basic_person_and_org(self) -> None:
        authors = {
            "https://bbc.com/article": AuthorInfo(person="Jane Doe", organization="BBC"),
        }
        facts = [_FakeFact() for _ in range(3)]
        result = authors_to_entity_suggestions(authors, facts)

        persons = [r for r in result if r["entity_subtype"] == "person"]
        orgs = [r for r in result if r["entity_subtype"] == "organization"]

        assert len(persons) == 1
        assert persons[0]["name"] == "Jane Doe"
        assert persons[0]["node_type"] == "entity"
        assert persons[0]["from_author_extraction"] is True
        assert persons[0]["fact_indices"] == [1, 2, 3]

        assert len(orgs) == 1
        assert orgs[0]["name"] == "BBC"

    def test_comma_separated_authors(self) -> None:
        authors = {
            "https://arxiv.org/paper": AuthorInfo(
                person="Ashish Vaswani, Noam Shazeer, Niki Parmar",
                organization="Google Brain",
            ),
        }
        facts = [_FakeFact(), _FakeFact()]
        result = authors_to_entity_suggestions(authors, facts)

        persons = [r for r in result if r["entity_subtype"] == "person"]
        assert len(persons) == 3
        names = {p["name"] for p in persons}
        assert "Ashish Vaswani" in names
        assert "Noam Shazeer" in names
        assert "Niki Parmar" in names

    def test_deduplicates_across_sources(self) -> None:
        authors = {
            "https://bbc.com/a": AuthorInfo(person="Jane", organization="BBC"),
            "https://bbc.com/b": AuthorInfo(person="John", organization="BBC"),
        }
        facts = [_FakeFact()]
        result = authors_to_entity_suggestions(authors, facts)

        orgs = [r for r in result if r["entity_subtype"] == "organization"]
        assert len(orgs) == 1  # BBC appears once

    def test_empty_authors(self) -> None:
        result = authors_to_entity_suggestions({}, [_FakeFact()])
        assert result == []

    def test_none_person_and_org(self) -> None:
        authors = {"https://x.com": AuthorInfo()}
        result = authors_to_entity_suggestions(authors, [_FakeFact()])
        assert result == []


class TestMergeExtractedNodes:
    def test_merges_duplicate_entity(self) -> None:
        llm_nodes = [
            {"name": "Jennifer Doudna", "node_type": "entity", "entity_subtype": "person", "fact_indices": [1, 2]},
            {"name": "CRISPR-Cas9", "node_type": "concept", "fact_indices": [1, 3]},
        ]
        author_nodes = [
            {"name": "Jennifer Doudna", "node_type": "entity", "entity_subtype": "person", "fact_indices": [3, 4], "from_author_extraction": True},
            {"name": "Nature", "node_type": "entity", "entity_subtype": "organization", "fact_indices": [1, 2, 3, 4], "from_author_extraction": True},
        ]
        result = merge_extracted_nodes(llm_nodes, author_nodes)

        by_name = {r["name"].lower(): r for r in result}
        assert len(result) == 3  # Doudna merged, CRISPR, Nature added

        # Doudna's fact_indices merged
        doudna = by_name["jennifer doudna"]
        assert sorted(doudna["fact_indices"]) == [1, 2, 3, 4]

        # Nature is new
        assert "nature" in by_name
        assert by_name["nature"]["entity_subtype"] == "organization"

    def test_empty_llm_nodes(self) -> None:
        author_nodes = [
            {"name": "BBC", "node_type": "entity", "entity_subtype": "organization", "fact_indices": [1]},
        ]
        result = merge_extracted_nodes([], author_nodes)
        assert len(result) == 1
        assert result[0]["name"] == "BBC"

    def test_empty_author_nodes(self) -> None:
        llm_nodes = [
            {"name": "CRISPR", "node_type": "concept", "fact_indices": [1]},
        ]
        result = merge_extracted_nodes(llm_nodes, [])
        assert len(result) == 1

    def test_case_insensitive_merge(self) -> None:
        llm_nodes = [
            {"name": "BBC", "node_type": "entity", "entity_subtype": "organization", "fact_indices": [1]},
        ]
        author_nodes = [
            {"name": "bbc", "node_type": "entity", "entity_subtype": "organization", "fact_indices": [2]},
        ]
        result = merge_extracted_nodes(llm_nodes, author_nodes)
        assert len(result) == 1
        assert sorted(result[0]["fact_indices"]) == [1, 2]
