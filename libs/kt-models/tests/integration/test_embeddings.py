import pytest

from kt_config.settings import get_settings
from kt_models.embeddings import EmbeddingService
from kt_models.expense import ExpenseContext, expense_scope

pytestmark = pytest.mark.asyncio

_TEST_EXPENSE = ExpenseContext(task_type="test_integration")


async def test_embed_text() -> None:
    """Integration test: call real embedding API."""
    settings = get_settings()
    if not settings.openrouter_api_key:
        pytest.skip("OPENROUTER_API_KEY not set")

    service = EmbeddingService()
    with expense_scope(_TEST_EXPENSE):
        embedding = await service.embed_text("Water boils at 100 degrees Celsius")

    assert isinstance(embedding, list)
    assert len(embedding) == settings.embedding_dimensions
    assert all(isinstance(x, (int, float)) for x in embedding)


async def test_embed_batch() -> None:
    """Integration test: batch embedding."""
    settings = get_settings()
    if not settings.openrouter_api_key:
        pytest.skip("OPENROUTER_API_KEY not set")

    service = EmbeddingService()
    texts = ["Hello world", "Goodbye world"]
    with expense_scope(_TEST_EXPENSE):
        embeddings = await service.embed_batch(texts)

    assert len(embeddings) == 2
    assert all(len(e) == settings.embedding_dimensions for e in embeddings)


async def test_embed_batch_empty() -> None:
    """Empty input should return empty list."""
    service = EmbeddingService()
    result = await service.embed_batch([])
    assert result == []
