"""Fact type specifications for the extraction pipeline.

Each FactTypeSpec captures the metadata currently embedded in the extraction
prompt: the enum value, a human-readable description, and the length rule
that guides the LLM on how verbose each fact should be.
"""

from __future__ import annotations

from dataclasses import dataclass

from kt_config.types import COMPOUND_FACT_TYPES, FactType


@dataclass(frozen=True)
class FactTypeSpec:
    """Descriptor for a single fact type used by the extraction pipeline."""

    fact_type: FactType
    description: str
    length_rule: str

    @property
    def is_compound(self) -> bool:
        """Whether this type allows multi-sentence / structured content."""
        return self.fact_type.value in COMPOUND_FACT_TYPES

    @property
    def name(self) -> str:
        return self.fact_type.value

    def render(self) -> str:
        """Render as a markdown bullet for inclusion in a prompt."""
        return f"- **{self.name}** \u2014 {self.description} ({self.length_rule})"


# ── All 12 fact type specs ───────────────────────────────────────────

CLAIM = FactTypeSpec(
    fact_type=FactType.claim,
    description=(
        "A statement asserted by a person, institution, or source. May or may not "
        "be true. Use for opinions, observations, informal definitions, and any "
        "general assertion."
    ),
    length_rule="1-2 sentences",
)

ACCOUNT = FactTypeSpec(
    fact_type=FactType.account,
    description=(
        "A first-person or narrative retelling of events or experiences. Use for "
        "historical narratives, testimonies, stories."
    ),
    length_rule="can be multi-sentence",
)

MEASUREMENT = FactTypeSpec(
    fact_type=FactType.measurement,
    description=("A quantitative data point with units, as reported by a source. Not inherently verified."),
    length_rule="1-2 sentences",
)

FORMULA = FactTypeSpec(
    fact_type=FactType.formula,
    description=(
        "A mathematical, logical, or formal statement that is true by definition "
        "within its formal system. Only use for provably correct formal statements."
    ),
    length_rule="1-2 sentences",
)

QUOTE = FactTypeSpec(
    fact_type=FactType.quote,
    description=("Verbatim text from a person, document, poem, speech, or book. Preserve exactly as written."),
    length_rule="can be multi-sentence",
)

PROCEDURE = FactTypeSpec(
    fact_type=FactType.procedure,
    description=("A step-by-step process, algorithm, recipe, or method. Preserve ordering and structure."),
    length_rule="can be multi-step",
)

REFERENCE = FactTypeSpec(
    fact_type=FactType.reference,
    description=(
        "An extract or summary from a book, paper, specification, or document. "
        "Preserves the source's framing. Use for architecture descriptions, "
        "detailed explanations, technical overviews. Always include the source"
        "when creating this fact type"
    ),
    length_rule="can be multi-sentence",
)

CODE = FactTypeSpec(
    fact_type=FactType.code,
    description=("A code snippet, configuration, command, or technical artifact. Preserve formatting exactly."),
    length_rule="can be multi-line",
)

IMAGE = FactTypeSpec(
    fact_type=FactType.image,
    description=("A description of visual content extracted from an image, chart, diagram, or infographic."),
    length_rule="1-3 sentences",
)

PERSPECTIVE = FactTypeSpec(
    fact_type=FactType.perspective,
    description=(
        "An opinionated stance, viewpoint, or position on a topic. Use for editorial "
        "opinions, policy positions, ideological arguments, and any subjective take "
        "that a person, group, or institution holds. Must clearly state the position."
    ),
    length_rule="1-2 sentences",
)

# ── Registries ───────────────────────────────────────────────────────

ALL_FACT_TYPES: tuple[FactTypeSpec, ...] = (
    CLAIM,
    ACCOUNT,
    MEASUREMENT,
    FORMULA,
    QUOTE,
    PROCEDURE,
    REFERENCE,
    CODE,
    IMAGE,
    PERSPECTIVE,
)

FACT_TYPE_BY_NAME: dict[str, FactTypeSpec] = {spec.name: spec for spec in ALL_FACT_TYPES}

# Types applicable to image extraction (no account, formula, code).
IMAGE_FACT_TYPES: tuple[FactTypeSpec, ...] = (
    CLAIM,
    MEASUREMENT,
    IMAGE,
    REFERENCE,
    PROCEDURE,
    QUOTE,
    PERSPECTIVE,
)
