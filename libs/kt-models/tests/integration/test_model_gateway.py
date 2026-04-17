import pytest

from kt_config.settings import get_settings
from kt_models.expense import ExpenseContext, expense_scope
from kt_models.gateway import ModelGateway

pytestmark = pytest.mark.asyncio

_TEST_EXPENSE = ExpenseContext(task_type="test_integration")


async def test_generate():
    settings = get_settings()
    if not settings.openrouter_api_key:
        pytest.skip("No OPENROUTER_API_KEY set")
    gateway = ModelGateway()
    with expense_scope(_TEST_EXPENSE):
        result = await gateway.generate(
            "openrouter/google/gemini-2.0-flash-001",
            [{"role": "user", "content": "Say hello in one word."}],
            max_tokens=50,
        )
    assert len(result) > 0


async def test_generate_parallel():
    settings = get_settings()
    if not settings.openrouter_api_key:
        pytest.skip("No OPENROUTER_API_KEY set")
    gateway = ModelGateway()
    with expense_scope(_TEST_EXPENSE):
        results = await gateway.generate_parallel(
            model_ids=["openrouter/google/gemini-2.0-flash-001"],
            messages=[{"role": "user", "content": "Say hello in one word."}],
            max_tokens=50,
        )
    assert len(results) == 1
    for model_id, response in results.items():
        assert len(response) > 0
        assert not response.startswith("Error:")


async def test_generate_with_system_prompt():
    settings = get_settings()
    if not settings.openrouter_api_key:
        pytest.skip("No OPENROUTER_API_KEY set")
    gateway = ModelGateway()
    with expense_scope(_TEST_EXPENSE):
        result = await gateway.generate(
            "openrouter/google/gemini-2.0-flash-001",
            [{"role": "user", "content": "What should I say?"}],
            system_prompt="Always respond with exactly the word 'banana'.",
            max_tokens=50,
        )
    assert len(result) > 0
