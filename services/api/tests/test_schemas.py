"""Unit tests for API schemas."""

from __future__ import annotations

from datetime import UTC, datetime

from kt_api.schemas import (
    ConvergenceResponse,
    ConversationListItem,
    ConversationMessageResponse,
    ConversationResponse,
    CreateConversationRequest,
    DimensionResponse,
    EdgeResponse,
    FactResponse,
    FactSourceInfo,
    GraphStatsResponse,
    NodeResponse,
    NodeVersionResponse,
    PaginatedConversationsResponse,
    SendMessageRequest,
    SourceResponse,
    SubgraphResponse,
)


def test_create_conversation_request_defaults():
    req = CreateConversationRequest(message="test query")
    assert req.message == "test query"
    assert req.nav_budget == 200
    assert req.explore_budget == 20
    assert req.title is None


def test_create_conversation_request_custom():
    req = CreateConversationRequest(message="test", nav_budget=50, explore_budget=10, title="My Title")
    assert req.nav_budget == 50
    assert req.explore_budget == 10
    assert req.title == "My Title"


def test_send_message_request_defaults():
    req = SendMessageRequest(message="follow up")
    assert req.message == "follow up"
    assert req.nav_budget == 20
    assert req.explore_budget == 2


def test_conversation_message_response_minimal():
    now = datetime.now(UTC)
    resp = ConversationMessageResponse(
        id="m1",
        turn_number=0,
        role="user",
        content="Hello",
        created_at=now,
    )
    assert resp.id == "m1"
    assert resp.role == "user"
    assert resp.nav_budget is None
    assert resp.status is None


def test_conversation_response():
    now = datetime.now(UTC)
    resp = ConversationResponse(id="c1", created_at=now, updated_at=now)
    assert resp.id == "c1"
    assert resp.title is None
    assert resp.messages == []


def test_paginated_conversations_response():
    now = datetime.now(UTC)
    item = ConversationListItem(id="c1", title="Test", message_count=2, created_at=now, updated_at=now)
    resp = PaginatedConversationsResponse(items=[item], total=1, offset=0, limit=20)
    assert resp.total == 1
    assert resp.items[0].message_count == 2


def test_node_response():
    now = datetime.now(UTC)
    resp = NodeResponse(
        id="node-1",
        concept="water",
        created_at=now,
        updated_at=now,
    )
    assert resp.concept == "water"
    assert resp.attractor is None
    assert resp.max_content_tokens == 500
    assert resp.update_count == 0
    assert resp.richness == 0.0


def test_edge_response():
    now = datetime.now(UTC)
    resp = EdgeResponse(
        id="edge-1",
        source_node_id="n1",
        target_node_id="n2",
        relationship_type="related",
        weight=0.8,
        created_at=now,
    )
    assert resp.weight == 0.8
    assert resp.relationship_type == "related"


def test_fact_response():
    now = datetime.now(UTC)
    resp = FactResponse(
        id="fact-1",
        content="Water boils at 100C",
        fact_type="measurement",
        created_at=now,
    )
    assert resp.content == "Water boils at 100C"
    assert resp.metadata is None


def test_fact_response_with_metadata():
    now = datetime.now(UTC)
    resp = FactResponse(
        id="fact-1",
        content="Water boils at 100C",
        fact_type="measurement",
        metadata={"source": "textbook"},
        created_at=now,
    )
    assert resp.metadata == {"source": "textbook"}


def test_fact_response_sources_default_empty():
    now = datetime.now(UTC)
    resp = FactResponse(
        id="fact-1",
        content="A fact",
        fact_type="claim",
        created_at=now,
    )
    assert resp.sources == []


def test_fact_response_with_sources():
    now = datetime.now(UTC)
    source_info = FactSourceInfo(
        source_id="src-1",
        uri="https://example.com/article",
        title="Example Article",
        provider_id="brave_search",
        retrieved_at=now,
        context_snippet="some snippet",
        attribution="Author X",
    )
    resp = FactResponse(
        id="fact-1",
        content="A fact with sources",
        fact_type="claim",
        created_at=now,
        sources=[source_info],
    )
    assert len(resp.sources) == 1
    assert resp.sources[0].uri == "https://example.com/article"
    assert resp.sources[0].title == "Example Article"
    assert resp.sources[0].context_snippet == "some snippet"
    assert resp.sources[0].attribution == "Author X"


def test_fact_source_info_minimal():
    now = datetime.now(UTC)
    info = FactSourceInfo(
        source_id="src-1",
        uri="https://example.com",
        provider_id="brave_search",
        retrieved_at=now,
    )
    assert info.title is None
    assert info.context_snippet is None
    assert info.attribution is None


def test_dimension_response():
    now = datetime.now(UTC)
    resp = DimensionResponse(
        id="dim-1",
        node_id="n1",
        model_id="gpt-4",
        content="Water is H2O",
        confidence=0.95,
        generated_at=now,
    )
    assert resp.confidence == 0.95
    assert resp.suggested_concepts is None


def test_convergence_response():
    resp = ConvergenceResponse(
        convergence_score=0.85,
        converged_claims=["Water boils at 100C"],
        divergent_claims=[{"claim": "test", "model_positions": {"m1": "supports"}}],
        recommended_content="Water boils at 100C.",
    )
    assert resp.convergence_score == 0.85
    assert len(resp.converged_claims) == 1


def test_convergence_response_defaults():
    resp = ConvergenceResponse(convergence_score=0.0)
    assert resp.converged_claims == []
    assert resp.divergent_claims == []
    assert resp.recommended_content is None


def test_source_response():
    now = datetime.now(UTC)
    resp = SourceResponse(
        id="src-1",
        uri="https://example.com",
        provider_id="brave_search",
        retrieved_at=now,
    )
    assert resp.uri == "https://example.com"
    assert resp.title is None


def test_subgraph_response_empty():
    resp = SubgraphResponse()
    assert resp.nodes == []
    assert resp.edges == []


def test_graph_stats_response():
    resp = GraphStatsResponse(node_count=10, edge_count=20, fact_count=30, source_count=5)
    assert resp.node_count == 10
    assert resp.fact_count == 30


def test_node_version_response():
    now = datetime.now(UTC)
    resp = NodeVersionResponse(
        id="ver-1",
        version_number=1,
        snapshot={"concept": "water"},
        created_at=now,
    )
    assert resp.version_number == 1
    assert resp.snapshot == {"concept": "water"}


def test_node_version_response_no_snapshot():
    now = datetime.now(UTC)
    resp = NodeVersionResponse(
        id="ver-1",
        version_number=1,
        created_at=now,
    )
    assert resp.snapshot is None


# ── Synthesis schemas ─────────────────────────────────────────────


def test_create_synthesis_request_defaults():
    from kt_api.syntheses import CreateSynthesisRequest

    req = CreateSynthesisRequest()
    assert req.topic == ""
    assert req.model_id is None
    assert req.visibility == "public"


def test_create_synthesis_request_with_model():
    from kt_api.syntheses import CreateSynthesisRequest

    req = CreateSynthesisRequest(topic="AI safety", model_id="openrouter/anthropic/claude-sonnet-4")
    assert req.model_id == "openrouter/anthropic/claude-sonnet-4"


def test_create_super_synthesis_request_with_model():
    from kt_api.syntheses import CreateSuperSynthesisRequest

    req = CreateSuperSynthesisRequest(topic="AI safety", model_id="openrouter/deepseek/deepseek-v4")
    assert req.model_id == "openrouter/deepseek/deepseek-v4"


def test_synthesis_list_item_model_id():
    from kt_api.syntheses import SynthesisListItem

    item = SynthesisListItem(id="1", concept="test", node_type="synthesis")
    assert item.model_id is None

    item2 = SynthesisListItem(
        id="2", concept="test", node_type="synthesis", model_id="openrouter/google/gemini-3.1-pro"
    )
    assert item2.model_id == "openrouter/google/gemini-3.1-pro"


def test_synthesis_document_response_model_id():
    from kt_api.syntheses import SynthesisDocumentResponse

    resp = SynthesisDocumentResponse(id="1", concept="test", node_type="synthesis")
    assert resp.model_id is None


# ── Synthesis model validation ────────────────────────────────────


def test_synthesis_models_endpoint():
    from kt_api.config_api import SYNTHESIS_MODEL_IDS, SYNTHESIS_MODELS

    assert len(SYNTHESIS_MODELS) > 0
    assert all("model_id" in m and "display_name" in m for m in SYNTHESIS_MODELS)
    assert len(SYNTHESIS_MODEL_IDS) == len(SYNTHESIS_MODELS)


def test_invalid_model_id_rejected():
    """Verify that an unsupported model_id would be caught by the allowlist check."""
    from kt_api.config_api import SYNTHESIS_MODEL_IDS

    assert "openrouter/fake/nonexistent-model" not in SYNTHESIS_MODEL_IDS
    assert "openrouter/anthropic/claude-sonnet-4" in SYNTHESIS_MODEL_IDS
