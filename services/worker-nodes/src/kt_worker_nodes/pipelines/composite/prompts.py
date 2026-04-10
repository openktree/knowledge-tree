"""System prompts for composite node agents.

The canonical prompt constants live in ``kt_agents_core.prompts.composite``.
This module re-exports them for backwards compatibility within the worker.
"""

from kt_agents_core.prompts.composite import COMPOSITE_SYNTHESIS_SYSTEM_PROMPT as SYNTHESIS_SYSTEM_PROMPT
from kt_agents_core.prompts.composite import PERSPECTIVE_SYSTEM_PROMPT

__all__ = [
    "PERSPECTIVE_SYSTEM_PROMPT",
    "SYNTHESIS_SYSTEM_PROMPT",
]
