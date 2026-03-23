"""Prompt builder for the fact extraction pipeline.

Assembles extraction prompts dynamically from FactTypeSpec, ExtractionConstraint,
and ExtractionExample instances. Replaces the monolithic _EXTRACT_PROMPT and
_IMAGE_EXTRACT_PROMPT strings.
"""

from __future__ import annotations

from collections.abc import Sequence

from kt_facts.prompt.constraints import (
    ALL_IMAGE_CONSTRAINTS,
    ALL_TEXT_CONSTRAINTS,
    ExtractionConstraint,
)
from kt_facts.prompt.examples import (
    ALL_IMAGE_EXAMPLES,
    ALL_TEXT_EXAMPLES,
    TEST_BEFORE_EXTRACTING,
    ExtractionExample,
)
from kt_facts.prompt.types import ALL_FACT_TYPES, IMAGE_FACT_TYPES, FactTypeSpec


class ExtractionPromptBuilder:
    """Assembles extraction prompts from composable parts."""

    def __init__(
        self,
        fact_types: Sequence[FactTypeSpec],
        constraints: Sequence[ExtractionConstraint],
        examples: Sequence[ExtractionExample],
    ) -> None:
        self._fact_types = fact_types
        self._constraints = sorted(constraints, key=lambda c: c.priority)
        self._examples = examples

    def build_text_prompt(
        self,
        concept: str,
        source_text: str,
        query_context: str | None = None,
        source_url: str | None = None,
        source_title: str | None = None,
    ) -> str:
        """Build a complete text extraction prompt."""
        sections: list[str] = []

        # 1. System intro
        sections.append(
            f"You are a fact extraction and attribution system. "
            f'Given the source text below about "{concept}", extract ALL knowledge worth preserving.'
        )

        # 1b. Source provenance (helps disambiguate entities/events)
        if source_url or source_title:
            provenance_parts = ["## Source provenance\n"]
            provenance_parts.append(
                "Use this metadata to disambiguate entities, events, and references in the text. "
                "For example, if the URL is from sec.gov and the text mentions 'the lawsuit', "
                "you can infer which specific lawsuit is meant."
            )
            if source_title:
                provenance_parts.append(f"- **Title**: {source_title}")
            if source_url:
                provenance_parts.append(f"- **URL**: {source_url}")
            sections.append("\n".join(provenance_parts))

        # 2. Fact types
        sections.append(self._render_fact_types())

        # 3. Constraints (sorted by priority)
        for constraint in self._constraints:
            sections.append(constraint.render())

        # 4. Examples
        examples_section = self._render_examples()
        if examples_section:
            sections.append(examples_section)

        # 5. Investigation context (optional)
        if query_context:
            sections.append(self._render_query_context(query_context))

        # 6. Response format
        sections.append(self._render_response_format())

        # 7. Source text
        sections.append(f'Source text:\n"""{source_text}"""')

        return "\n\n".join(sections)

    def build_image_prompt(
        self,
        concept: str,
        query_context: str | None = None,
    ) -> str:
        """Build a complete image extraction prompt."""
        sections: list[str] = []

        # 1. System intro
        sections.append(
            f'You are a visual fact extraction system. Analyze the image below about "{concept}" '
            f"and extract ALL knowledge worth preserving."
        )

        # 2. Fact types
        sections.append(self._render_fact_types())

        # 3. Constraints (sorted by priority)
        for constraint in self._constraints:
            sections.append(constraint.render())

        # 4. Examples
        examples_section = self._render_examples()
        if examples_section:
            sections.append(examples_section)

        # 5. Investigation context (optional)
        if query_context:
            sections.append(self._render_image_query_context(query_context))

        # 6. Response format
        sections.append(self._render_response_format())

        return "\n\n".join(sections)

    # ── Private rendering helpers ────────────────────────────────────

    def _render_fact_types(self) -> str:
        lines = ["## Fact types", ""]
        lines.append(
            "Each fact must be assigned one of the following types. Use the description to decide which type fits best."
        )
        lines.append("")
        for spec in self._fact_types:
            lines.append(spec.render())
        return "\n".join(lines)

    def _render_examples(self) -> str:
        if not self._examples:
            return ""

        bad = [e for e in self._examples if not e.is_good]
        good = [e for e in self._examples if e.is_good]

        lines = ["Examples:", ""]
        for e in bad:
            lines.append(e.render())
            lines.append("")
        for e in good:
            lines.append(e.render())
            lines.append("")

        lines.append(TEST_BEFORE_EXTRACTING)
        return "\n".join(lines)

    def _render_query_context(self, query_context: str) -> str:
        return (
            "## Investigation context\n\n"
            f'The user is investigating: "{query_context}"\n\n'
            "While you must still extract ALL facts from the text (not just those relevant to the "
            "investigation), be especially thorough about extracting evidence that relates to this "
            "investigation \u2014 including evidence that supports, challenges, or provides nuance to the "
            "topic. Do NOT skip facts unrelated to the investigation; extract everything. But ensure "
            "nothing relevant to the investigation is missed."
        )

    def _render_image_query_context(self, query_context: str) -> str:
        return (
            "## Investigation context\n\n"
            f'The user is investigating: "{query_context}"\n\n'
            "Be especially thorough about extracting visual evidence that relates to this investigation."
        )

    def _render_response_format(self) -> str:
        return (
            "## Response format\n\n"
            "Respond with a JSON object:\n"
            '{{"facts": [\n'
            "  {{\n"
            '    "content": "the extracted content",\n'
            '    "fact_type": "one_of_the_types",\n'
            '    "who": "person or organization (or null)",\n'
            '    "where": "location or publication (or null)",\n'
            '    "when": "date or time period (or null)",\n'
            '    "context": "brief context about the source (or null)"\n'
            "  }}\n"
            "]}}\n\n"
            'If no facts can be extracted, return {{"facts": []}}.'
        )


# ── Default builders ─────────────────────────────────────────────────

TEXT_PROMPT_BUILDER = ExtractionPromptBuilder(
    fact_types=ALL_FACT_TYPES,
    constraints=ALL_TEXT_CONSTRAINTS,
    examples=ALL_TEXT_EXAMPLES,
)

IMAGE_PROMPT_BUILDER = ExtractionPromptBuilder(
    fact_types=IMAGE_FACT_TYPES,
    constraints=ALL_IMAGE_CONSTRAINTS,
    examples=ALL_IMAGE_EXAMPLES,
)
