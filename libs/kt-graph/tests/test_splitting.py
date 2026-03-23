import uuid
from types import SimpleNamespace

from kt_graph.splitting import _cluster_facts, _clusters_are_contradictory, _fact_similarity


def _make_fact(content: str, fact_type: str = "claim") -> SimpleNamespace:
    """Create a mock Fact object with the required attributes."""
    return SimpleNamespace(id=uuid.uuid4(), content=content, fact_type=fact_type)


class TestFactSimilarity:
    def test_identical_facts(self):
        fa = _make_fact("Water boils at 100 degrees Celsius")
        fb = _make_fact("Water boils at 100 degrees Celsius")
        sim = _fact_similarity(fa, fb)  # type: ignore[arg-type]
        assert sim > 0.8

    def test_similar_facts(self):
        fa = _make_fact("Water boils at 100 degrees Celsius under standard pressure")
        fb = _make_fact("Water boils at approximately 100 degrees Celsius")
        sim = _fact_similarity(fa, fb)  # type: ignore[arg-type]
        assert sim > 0.3

    def test_unrelated_facts(self):
        fa = _make_fact("Water boils at 100 degrees Celsius")
        fb = _make_fact("The French Revolution started in 1789")
        sim = _fact_similarity(fa, fb)  # type: ignore[arg-type]
        assert sim < 0.15

    def test_empty_content(self):
        fa = _make_fact("")
        fb = _make_fact("Water boils at 100 degrees")
        sim = _fact_similarity(fa, fb)  # type: ignore[arg-type]
        assert sim == 0.0


class TestClusterFacts:
    def test_single_fact(self):
        facts = [_make_fact("Water boils at 100 degrees")]
        clusters = _cluster_facts(facts)  # type: ignore[arg-type]
        assert len(clusters) == 1
        assert len(clusters[0]) == 1

    def test_similar_facts_cluster_together(self):
        facts = [
            _make_fact("Water boils at 100 degrees Celsius under standard pressure conditions"),
            _make_fact("Water reaches boiling point at 100 degrees Celsius at sea level pressure"),
            _make_fact("The French Revolution began in 1789 with the storming of the Bastille"),
        ]
        clusters = _cluster_facts(facts)  # type: ignore[arg-type]
        # Should have 2 clusters: water facts together, French Revolution separate
        assert len(clusters) >= 2

    def test_unrelated_facts_separate(self):
        facts = [
            _make_fact("Water molecules consist of hydrogen and oxygen atoms bonded"),
            _make_fact("The French Revolution drastically changed European political landscape"),
            _make_fact("Quantum entanglement connects particles across vast distances instantly"),
        ]
        clusters = _cluster_facts(facts)  # type: ignore[arg-type]
        assert len(clusters) >= 2

    def test_empty_facts(self):
        clusters = _cluster_facts([])
        assert len(clusters) == 0


class TestClustersAreContradictory:
    def test_single_cluster_not_contradictory(self):
        facts = [_make_fact("Water boils at 100 degrees")]
        clusters = _cluster_facts(facts)  # type: ignore[arg-type]
        assert not _clusters_are_contradictory(clusters)  # type: ignore[arg-type]

    def test_dissimilar_clusters_are_contradictory(self):
        cluster_a = [_make_fact("Water boils at 100 degrees Celsius under standard conditions")]
        cluster_b = [_make_fact("The French Revolution fundamentally transformed European politics")]
        assert _clusters_are_contradictory([cluster_a, cluster_b])  # type: ignore[list-item]

    def test_similar_clusters_not_contradictory(self):
        cluster_a = [_make_fact("Water boils at 100 degrees Celsius under standard pressure")]
        cluster_b = [_make_fact("Water reaches boiling temperature around 100 degrees Celsius")]
        assert not _clusters_are_contradictory([cluster_a, cluster_b])  # type: ignore[list-item]
