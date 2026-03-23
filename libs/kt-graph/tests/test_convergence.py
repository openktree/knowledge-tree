from types import SimpleNamespace

from kt_graph.convergence import (
    _claims_match,
    _extract_claims,
    _tokenize,
    compute_convergence,
)


def _make_dimension(model_id: str, content: str) -> SimpleNamespace:
    """Create a mock Dimension object with the required attributes."""
    return SimpleNamespace(model_id=model_id, content=content)


class TestTokenize:
    def test_removes_stopwords(self):
        tokens = _tokenize("the water is cold and the ice is frozen")
        assert "the" not in tokens
        assert "is" not in tokens
        assert "and" not in tokens
        assert "water" in tokens
        assert "cold" in tokens
        assert "ice" in tokens

    def test_lowercase(self):
        tokens = _tokenize("Water Boils At 100 Degrees")
        assert "water" in tokens
        assert "boils" in tokens

    def test_filters_single_char(self):
        tokens = _tokenize("a b c water d")
        assert "water" in tokens
        assert "b" not in tokens


class TestExtractClaims:
    def test_splits_sentences(self):
        text = "Water boils at 100 degrees. Ice melts at zero degrees."
        claims = _extract_claims(text)
        assert len(claims) == 2

    def test_filters_short_fragments(self):
        text = "Yes. No. Water boils at 100 degrees Celsius under standard pressure."
        claims = _extract_claims(text)
        # "Yes" and "No" should be filtered out (less than 3 content words)
        assert all(len(_tokenize(c)) >= 3 for c in claims)

    def test_handles_bullet_points(self):
        text = "- Water boils at 100 degrees\n- Ice melts at zero degrees\n- Steam condenses to water"
        claims = _extract_claims(text)
        assert len(claims) >= 2

    def test_handles_numbered_lists(self):
        text = "1. Water boils at 100 degrees\n2. Ice melts at zero degrees"
        claims = _extract_claims(text)
        assert len(claims) >= 2


class TestClaimsMatch:
    def test_identical_claims(self):
        assert _claims_match("Water boils at 100 degrees", "Water boils at 100 degrees")

    def test_similar_claims(self):
        assert _claims_match(
            "Water boils at 100 degrees Celsius",
            "Water boils at approximately 100 degrees Celsius",
        )

    def test_dissimilar_claims(self):
        assert not _claims_match(
            "Water boils at 100 degrees",
            "The economy grew by 3 percent last year",
        )


class TestComputeConvergence:
    def test_empty_dimensions(self):
        result = compute_convergence([])
        assert result["convergence_score"] == 0.0
        assert result["converged_claims"] == []
        assert result["divergent_claims"] == []

    def test_single_dimension(self):
        dim = _make_dimension("model-a", "Water boils at 100 degrees. Ice melts at zero degrees.")
        result = compute_convergence([dim])  # type: ignore[list-item]
        assert result["convergence_score"] == 1.0
        assert len(result["converged_claims"]) >= 1

    def test_full_convergence(self):
        dims = [
            _make_dimension("model-a", "Water boils at 100 degrees Celsius under standard pressure."),
            _make_dimension("model-b", "Water boils at 100 degrees Celsius under standard atmospheric pressure."),
        ]
        result = compute_convergence(dims)  # type: ignore[list-item]
        assert result["convergence_score"] > 0.5
        assert len(result["converged_claims"]) >= 1

    def test_partial_divergence(self):
        dims = [
            _make_dimension(
                "model-a",
                "Water boils at 100 degrees Celsius. The sky appears blue due to Rayleigh scattering.",
            ),
            _make_dimension(
                "model-b",
                "Water boils at 100 degrees Celsius. The economy is growing steadily this year.",
            ),
        ]
        result = compute_convergence(dims)  # type: ignore[list-item]
        score = result["convergence_score"]
        assert isinstance(score, float)
        # There should be some convergence (the water claim) and some divergence
        assert 0.0 < score < 1.0
        assert len(result["converged_claims"]) >= 1
        assert len(result["divergent_claims"]) >= 1

    def test_complete_disagreement(self):
        dims = [
            _make_dimension("model-a", "Quantum mechanics describes subatomic particle behavior precisely."),
            _make_dimension("model-b", "Renaissance art flourished in 15th century Florence Italy."),
        ]
        result = compute_convergence(dims)  # type: ignore[list-item]
        assert result["convergence_score"] == 0.0
        assert len(result["converged_claims"]) == 0
        assert len(result["divergent_claims"]) >= 1

    def test_recommended_content_from_converged(self):
        dims = [
            _make_dimension("model-a", "Water is composed of hydrogen and oxygen atoms bonded together."),
            _make_dimension("model-b", "Water is composed of hydrogen and oxygen atoms chemically bonded."),
        ]
        result = compute_convergence(dims)  # type: ignore[list-item]
        if result["converged_claims"]:
            assert len(result["recommended_content"]) > 0

    def test_divergent_claims_have_model_positions(self):
        dims = [
            _make_dimension(
                "model-a",
                "Water boils at 100 degrees. Photosynthesis converts sunlight into chemical energy.",
            ),
            _make_dimension("model-b", "Water boils at 100 degrees. The stock market reached new highs recently."),
        ]
        result = compute_convergence(dims)  # type: ignore[list-item]
        for dc in result["divergent_claims"]:
            assert "claim" in dc
            assert "model_positions" in dc
            positions = dc["model_positions"]
            assert isinstance(positions, dict)
            assert "model-a" in positions or "model-b" in positions
