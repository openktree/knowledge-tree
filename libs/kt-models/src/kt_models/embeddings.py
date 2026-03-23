import asyncio
import logging
from typing import Any

from litellm import aembedding

from kt_config.settings import get_settings

logger = logging.getLogger(__name__)

_MAX_RETRIES = 2


def _record_embedding_usage(response: Any, model: str) -> None:
    """Record embedding token usage to the ContextVar accumulator."""
    from kt_models.usage import record_usage

    usage = getattr(response, "usage", None)
    if usage is None:
        return
    prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
    total_tokens = getattr(usage, "total_tokens", 0) or 0
    if prompt_tokens == 0 and total_tokens == 0:
        return

    # OpenRouter returns cost directly on the usage object
    cost_usd = float(getattr(usage, "cost", 0) or 0)

    if cost_usd == 0.0:
        try:
            from litellm import completion_cost

            cost_usd = float(completion_cost(completion_response=response) or 0.0)
        except Exception:
            pass

    record_usage(model, prompt_tokens, total_tokens - prompt_tokens, cost_usd)


class EmbeddingService:
    """Wraps LiteLLM for text embedding generation via OpenRouter."""

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        timeout: int | None = None,
        chunk_size: int | None = None,
    ) -> None:
        settings = get_settings()
        self._model = model or settings.embedding_model
        self._api_key = api_key or settings.openrouter_api_key
        self._timeout = timeout or settings.embedding_timeout
        self._chunk_size = chunk_size or settings.embedding_batch_chunk_size

    async def embed_text(self, text: str) -> list[float]:
        """Generate embedding for a single text."""
        last_err: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                response = await aembedding(
                    model=self._model,
                    input=[text],
                    api_key=self._api_key,
                    api_base="https://openrouter.ai/api/v1",
                    encoding_format="float",
                    timeout=self._timeout,
                )
                _record_embedding_usage(response, self._model)
                return response.data[0]["embedding"]  # type: ignore[index]
            except Exception as exc:
                last_err = exc
                if attempt < _MAX_RETRIES:
                    logger.warning("embed_text failed (attempt %d), retrying: %s", attempt + 1, exc)
                    await asyncio.sleep(1)
        raise last_err  # type: ignore[misc]

    async def embed_batch(
        self, texts: list[str], *, chunk_size: int | None = None,
    ) -> list[list[float]]:
        """Generate embeddings for multiple texts.

        Splits into chunks of *chunk_size* to stay within API limits and avoid
        timeouts on large batches.  Defaults to ``self._chunk_size`` (from Settings).
        """
        if not texts:
            return []
        effective_chunk = chunk_size or self._chunk_size
        results: list[list[float]] = []
        for i in range(0, len(texts), effective_chunk):
            chunk = texts[i : i + effective_chunk]
            last_err: Exception | None = None
            for attempt in range(_MAX_RETRIES + 1):
                try:
                    response = await aembedding(
                        model=self._model,
                        input=chunk,
                        api_key=self._api_key,
                        api_base="https://openrouter.ai/api/v1",
                        encoding_format="float",
                        timeout=self._timeout,
                    )
                    _record_embedding_usage(response, self._model)
                    results.extend(item["embedding"] for item in response.data)  # type: ignore[index]
                    break
                except Exception as exc:
                    last_err = exc
                    if attempt < _MAX_RETRIES:
                        logger.warning("embed_batch chunk %d failed (attempt %d), retrying: %s", i, attempt + 1, exc)
                        await asyncio.sleep(1)
            else:
                raise last_err  # type: ignore[misc]
        return results
