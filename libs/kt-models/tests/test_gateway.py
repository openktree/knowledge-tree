import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kt_models.gateway import ModelGateway, _is_retryable

pytestmark = pytest.mark.asyncio


class TestModelGateway:
    @patch("kt_models.gateway.acompletion")
    async def test_generate(self, mock_acompletion: AsyncMock):
        # Mock the LiteLLM response structure
        mock_response = AsyncMock()
        mock_response.choices = [AsyncMock()]
        mock_response.choices[0].message.content = "Hello, world!"
        mock_acompletion.return_value = mock_response

        gateway = ModelGateway(api_key="test-key")
        result = await gateway.generate("test-model", [{"role": "user", "content": "Say hello"}])

        assert result == "Hello, world!"
        mock_acompletion.assert_called_once()

    @patch("kt_models.gateway.acompletion")
    async def test_generate_with_system_prompt(self, mock_acompletion: AsyncMock):
        mock_response = AsyncMock()
        mock_response.choices = [AsyncMock()]
        mock_response.choices[0].message.content = "Response"
        mock_acompletion.return_value = mock_response

        gateway = ModelGateway(api_key="test-key")
        await gateway.generate(
            "test-model",
            [{"role": "user", "content": "Hello"}],
            system_prompt="Be helpful",
        )

        # Verify system prompt was included in messages
        call_kwargs = mock_acompletion.call_args
        messages = call_kwargs.kwargs["messages"]
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "Be helpful"

    @patch("kt_models.gateway.acompletion")
    async def test_generate_empty_content(self, mock_acompletion: AsyncMock):
        mock_response = AsyncMock()
        mock_response.choices = [AsyncMock()]
        mock_response.choices[0].message.content = None
        mock_acompletion.return_value = mock_response

        gateway = ModelGateway(api_key="test-key")
        result = await gateway.generate("test-model", [{"role": "user", "content": "Hello"}])
        assert result == ""

    @patch("kt_models.gateway.acompletion")
    async def test_generate_parallel(self, mock_acompletion: AsyncMock):
        mock_response = AsyncMock()
        mock_response.choices = [AsyncMock()]
        mock_response.choices[0].message.content = "Result"
        mock_acompletion.return_value = mock_response

        gateway = ModelGateway(api_key="test-key")
        results = await gateway.generate_parallel(
            ["model-a", "model-b"],
            [{"role": "user", "content": "Hello"}],
        )

        assert len(results) == 2
        assert results["model-a"] == "Result"
        assert results["model-b"] == "Result"

    @patch("kt_models.gateway.acompletion")
    async def test_generate_parallel_handles_errors(self, mock_acompletion: AsyncMock):
        call_count = 0

        async def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("API rate limit")
            mock_resp = AsyncMock()
            mock_resp.choices = [AsyncMock()]
            mock_resp.choices[0].message.content = "Success"
            return mock_resp

        mock_acompletion.side_effect = side_effect

        gateway = ModelGateway(api_key="test-key")
        results = await gateway.generate_parallel(
            ["model-a", "model-b"],
            [{"role": "user", "content": "Hello"}],
        )

        assert len(results) == 2
        # One should have errored
        error_results = [v for v in results.values() if v.startswith("Error:")]
        success_results = [v for v in results.values() if v == "Success"]
        assert len(error_results) == 1
        assert len(success_results) == 1


def test_is_retryable_timeout() -> None:
    """Timeout errors should be retryable."""
    from litellm.exceptions import Timeout

    exc = Timeout(message="Connection timed out", model="test", llm_provider="openrouter")
    assert _is_retryable(exc) is True


def test_is_retryable_rate_limit() -> None:
    """RateLimitError should be retryable."""
    from litellm.exceptions import RateLimitError

    exc = RateLimitError(message="Rate limited", model="test", llm_provider="openrouter")
    assert _is_retryable(exc) is True


def test_is_retryable_generic_exception() -> None:
    """Generic exceptions should NOT be retryable."""
    assert _is_retryable(ValueError("bad input")) is False
    assert _is_retryable(RuntimeError("something broke")) is False


class TestCallWithRetry:
    @patch("kt_models.gateway.acompletion")
    @patch("kt_models.gateway.asyncio.sleep", new_callable=AsyncMock)
    async def test_retries_on_timeout(self, mock_sleep: AsyncMock, mock_acompletion: AsyncMock) -> None:
        """_call_with_retry retries on Timeout and succeeds on second attempt."""
        from litellm.exceptions import Timeout

        mock_response = AsyncMock()
        mock_response.choices = [AsyncMock()]
        mock_response.choices[0].message.content = "Success after retry"

        mock_acompletion.side_effect = [
            Timeout(message="timed out", model="test", llm_provider="openrouter"),
            mock_response,
        ]

        gateway = ModelGateway(api_key="test-key")
        result = await gateway.generate("test-model", [{"role": "user", "content": "Hello"}])

        assert result == "Success after retry"
        assert mock_acompletion.call_count == 2
        mock_sleep.assert_awaited_once()

    @patch("kt_models.gateway.acompletion")
    async def test_no_retry_on_non_retryable(self, mock_acompletion: AsyncMock) -> None:
        """_call_with_retry does NOT retry on non-retryable errors."""
        mock_acompletion.side_effect = ValueError("bad input")

        gateway = ModelGateway(api_key="test-key")
        with pytest.raises(ValueError, match="bad input"):
            await gateway.generate("test-model", [{"role": "user", "content": "Hello"}])

        assert mock_acompletion.call_count == 1


def _make_json_response(
    content: str,
    finish_reason: str = "stop",
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
) -> MagicMock:
    """Create a mock LiteLLM response with the given content and finish_reason."""
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.choices[0].finish_reason = finish_reason
    if prompt_tokens is not None or completion_tokens is not None:
        resp.usage.prompt_tokens = prompt_tokens
        resp.usage.completion_tokens = completion_tokens
    else:
        resp.usage = None
    return resp


class TestGenerateJsonRetry:
    """Tests for JSON parse retry logic in generate_json()."""

    @patch("kt_models.gateway.acompletion")
    async def test_succeeds_on_first_attempt(self, mock_acompletion: AsyncMock) -> None:
        """Valid JSON on first attempt — no retry needed."""
        mock_acompletion.return_value = _make_json_response('{"facts": []}')

        gateway = ModelGateway(api_key="test-key")
        result = await gateway.generate_json("test-model", [{"role": "user", "content": "extract"}])

        assert result == {"facts": []}
        assert mock_acompletion.call_count == 1

    @patch("kt_models.gateway.acompletion")
    async def test_retries_on_token_limit_then_succeeds(self, mock_acompletion: AsyncMock) -> None:
        """Truncated response (finish_reason='length') triggers retry, second attempt succeeds."""
        truncated = _make_json_response('{"facts": [{"content": "partial', finish_reason="length")
        valid = _make_json_response('{"facts": [{"content": "complete"}]}')
        mock_acompletion.side_effect = [truncated, valid]

        gateway = ModelGateway(api_key="test-key")
        result = await gateway.generate_json("test-model", [{"role": "user", "content": "extract"}])

        assert result == {"facts": [{"content": "complete"}]}
        assert mock_acompletion.call_count == 2
        # Verify retry message asks for concise output
        retry_msgs = mock_acompletion.call_args_list[1].kwargs["messages"]
        retry_user_msg = retry_msgs[-1]["content"]
        assert "token limit" in retry_user_msg
        assert "FEWER items" in retry_user_msg

    @patch("kt_models.gateway.acompletion")
    async def test_retries_on_format_error_then_succeeds(self, mock_acompletion: AsyncMock) -> None:
        """Non-JSON response (finish_reason='stop') triggers retry, second attempt succeeds."""
        bad_format = _make_json_response("I cannot produce JSON for this request.", finish_reason="stop")
        valid = _make_json_response('{"facts": []}')
        mock_acompletion.side_effect = [bad_format, valid]

        gateway = ModelGateway(api_key="test-key")
        result = await gateway.generate_json("test-model", [{"role": "user", "content": "extract"}])

        assert result == {"facts": []}
        assert mock_acompletion.call_count == 2
        # Verify retry message asks for valid JSON
        retry_msgs = mock_acompletion.call_args_list[1].kwargs["messages"]
        retry_user_msg = retry_msgs[-1]["content"]
        assert "not valid JSON" in retry_user_msg

    @patch("kt_models.gateway.acompletion")
    async def test_cleanup_extracts_json_from_markdown_fences(self, mock_acompletion: AsyncMock) -> None:
        """JSON wrapped in markdown code fences is extracted without retrying."""
        fenced = _make_json_response('Sure!\n```json\n{"facts": ["a", "b"]}\n```\n', finish_reason="stop")
        mock_acompletion.return_value = fenced

        gateway = ModelGateway(api_key="test-key")
        result = await gateway.generate_json("test-model", [{"role": "user", "content": "extract"}])

        assert result == {"facts": ["a", "b"]}
        assert mock_acompletion.call_count == 1  # No retry needed

    @patch("kt_models.gateway.acompletion")
    async def test_returns_empty_after_all_retries_exhausted(self, mock_acompletion: AsyncMock) -> None:
        """All 5 attempts fail — returns empty dict."""
        bad = _make_json_response("I'm sorry, I can't help with that.", finish_reason="stop")
        mock_acompletion.return_value = bad

        gateway = ModelGateway(api_key="test-key")
        result = await gateway.generate_json("test-model", [{"role": "user", "content": "extract"}])

        assert result == {}
        # 1 initial + 4 retries = 5 calls
        assert mock_acompletion.call_count == 5

    @patch("kt_models.gateway.acompletion")
    async def test_logs_token_limit_cause(self, mock_acompletion: AsyncMock, caplog: pytest.LogCaptureFixture) -> None:
        """Token limit cause is logged on retry."""
        truncated = _make_json_response('{"facts": [{"content": "par', finish_reason="length")
        valid = _make_json_response('{"facts": []}')
        mock_acompletion.side_effect = [truncated, valid]

        gateway = ModelGateway(api_key="test-key")
        with caplog.at_level(logging.WARNING, logger="kt_models.gateway"):
            await gateway.generate_json("test-model", [{"role": "user", "content": "extract"}])

        assert any("cause: token_limit" in rec.message for rec in caplog.records)
        assert any("finish_reason='length'" in rec.message for rec in caplog.records)

    @patch("kt_models.gateway.acompletion")
    async def test_logs_format_error_cause(self, mock_acompletion: AsyncMock, caplog: pytest.LogCaptureFixture) -> None:
        """Format error cause is logged on retry."""
        bad = _make_json_response("not json at all", finish_reason="stop")
        valid = _make_json_response('{"ok": true}')
        mock_acompletion.side_effect = [bad, valid]

        gateway = ModelGateway(api_key="test-key")
        with caplog.at_level(logging.WARNING, logger="kt_models.gateway"):
            await gateway.generate_json("test-model", [{"role": "user", "content": "extract"}])

        assert any("cause: format_error" in rec.message for rec in caplog.records)
        assert any("non-JSON output" in rec.message for rec in caplog.records)

    @patch("kt_models.gateway.acompletion")
    async def test_logs_final_failure_with_cause(
        self, mock_acompletion: AsyncMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Final failure log includes the last cause and attempt count."""
        bad = _make_json_response("This response has no JSON at all", finish_reason="length")
        mock_acompletion.return_value = bad

        gateway = ModelGateway(api_key="test-key")
        with caplog.at_level(logging.WARNING, logger="kt_models.gateway"):
            result = await gateway.generate_json("test-model", [{"role": "user", "content": "extract"}])

        assert result == {}
        final_logs = [r for r in caplog.records if "after 5 attempt(s)" in r.message]
        assert len(final_logs) == 1
        assert "last cause: token_limit" in final_logs[0].message

    @patch("kt_models.gateway.acompletion")
    async def test_logs_token_usage_on_token_limit(
        self, mock_acompletion: AsyncMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Token usage (prompt/completion/max) is included in retry logs."""
        truncated = _make_json_response(
            '{"facts": [{"content": "par',
            finish_reason="length",
            prompt_tokens=1200,
            completion_tokens=16000,
        )
        valid = _make_json_response('{"facts": []}')
        mock_acompletion.side_effect = [truncated, valid]

        gateway = ModelGateway(api_key="test-key")
        with caplog.at_level(logging.WARNING, logger="kt_models.gateway"):
            await gateway.generate_json("test-model", [{"role": "user", "content": "extract"}])

        token_logs = [r for r in caplog.records if "tokens:" in r.message]
        assert len(token_logs) >= 1
        assert "prompt=1200" in token_logs[0].message
        assert "completion=16000" in token_logs[0].message
        assert "max=16000" in token_logs[0].message

    @patch("kt_models.gateway.acompletion")
    async def test_logs_token_usage_on_final_failure(
        self, mock_acompletion: AsyncMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Token usage is included in the final failure log."""
        bad = _make_json_response(
            "This response has no JSON at all",
            finish_reason="length",
            prompt_tokens=800,
            completion_tokens=16000,
        )
        mock_acompletion.return_value = bad

        gateway = ModelGateway(api_key="test-key")
        with caplog.at_level(logging.WARNING, logger="kt_models.gateway"):
            await gateway.generate_json("test-model", [{"role": "user", "content": "extract"}])

        final_logs = [r for r in caplog.records if "after 5 attempt(s)" in r.message]
        assert len(final_logs) == 1
        assert "tokens:" in final_logs[0].message
        assert "prompt=800" in final_logs[0].message

    @patch("kt_models.gateway.acompletion")
    async def test_no_token_usage_when_unavailable(
        self, mock_acompletion: AsyncMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When usage is not present, logs omit the token section."""
        bad = _make_json_response("no json here", finish_reason="stop")  # usage=None
        valid = _make_json_response('{"ok": true}')
        mock_acompletion.side_effect = [bad, valid]

        gateway = ModelGateway(api_key="test-key")
        with caplog.at_level(logging.WARNING, logger="kt_models.gateway"):
            await gateway.generate_json("test-model", [{"role": "user", "content": "extract"}])

        assert not any("tokens:" in r.message for r in caplog.records)

    @patch("kt_models.gateway.acompletion")
    async def test_retry_preserves_original_messages(self, mock_acompletion: AsyncMock) -> None:
        """Retry messages are built from original messages, not compounding."""
        bad = _make_json_response("no json here", finish_reason="stop")
        valid = _make_json_response('{"ok": true}')
        mock_acompletion.side_effect = [bad, valid]

        original_msgs = [{"role": "user", "content": "extract facts"}]
        gateway = ModelGateway(api_key="test-key")
        await gateway.generate_json("test-model", original_msgs, system_prompt="Be precise")

        # Second call's messages should start with system + original user msg
        retry_msgs = mock_acompletion.call_args_list[1].kwargs["messages"]
        assert retry_msgs[0] == {"role": "system", "content": "Be precise"}
        assert retry_msgs[1] == {"role": "user", "content": "extract facts"}
        # Then assistant's broken response + retry hint
        assert retry_msgs[2]["role"] == "assistant"
        assert retry_msgs[3]["role"] == "user"
