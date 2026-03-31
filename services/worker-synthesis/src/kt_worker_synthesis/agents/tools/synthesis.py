"""Synthesis completion tool for the Synthesizer Agent."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool


def build_synthesis_tools(state_ref: list[Any]) -> list[BaseTool]:
    """Build the finish_synthesis tool."""

    @tool
    async def finish_synthesis(text: str) -> str:
        """Submit the final synthesis document. The text argument MUST contain the COMPLETE markdown text. Anything written outside this tool is discarded."""
        state = state_ref[0]
        if state is not None:
            from kt_models.link_normalizer import normalize_ai_links

            state.synthesis_text = normalize_ai_links(text)
            state.phase = "done"
        return "Synthesis document submitted."

    return [finish_synthesis]
