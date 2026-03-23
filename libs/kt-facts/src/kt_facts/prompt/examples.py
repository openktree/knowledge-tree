"""Extraction examples for the fact decomposition pipeline.

Each ExtractionExample captures a GOOD or BAD example that was previously
hardcoded in the prompt string, along with an explanation of why it is
good or bad.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExtractionExample:
    """A single example for the extraction prompt."""

    text: str
    is_good: bool
    explanation: str

    def render(self) -> str:
        """Render as a labelled line for inclusion in a prompt."""
        label = "GOOD" if self.is_good else "BAD"
        return f'{label}: "{self.text}"\n  \u2192 {self.explanation}'


# ── BAD examples ─────────────────────────────────────────────────────

BAD_TITLE = ExtractionExample(
    text="The Carbon Dioxide Laser",
    is_good=False,
    explanation="No predicate, no assertion. This is a title/label.",
)

BAD_HEADLINE = ExtractionExample(
    text="For The First Time, A pant that grows without ligth",
    is_good=False,
    explanation="Headline framing, no concrete claim. Who made it? When? How?",
)

BAD_FRAGMENT = ExtractionExample(
    text="On knowing Marlan",
    is_good=False,
    explanation="Fragment. No subject performing an action, no assertion.",
)

BAD_NOUN_PHRASE = ExtractionExample(
    text="Carbon dioxide laser applications",
    is_good=False,
    explanation="Bare noun phrase. No verb, no claim.",
)

BAD_VAGUE_SUBJECT = ExtractionExample(
    text="He was good",
    is_good=False,
    explanation="Vague subject, who is it about?",
)

BAD_VAGUE_LABEL = ExtractionExample(
    text="A new approach to water purification",
    is_good=False,
    explanation="Vague label. What approach? By whom? What makes it new?",
)

BAD_TOPIC_DESCRIPTION = ExtractionExample(
    text="The role of inflammation in disease",
    is_good=False,
    explanation="Topic description, not an assertion. What role specifically?",
)

BAD_MISSING_CONTEXT = ExtractionExample(
    text=(
        "Millions of visitors experienced electric lighting and saw AC motors, "
        "generators, and other equipment operating safely and reliably at the fair."
    ),
    is_good=False,
    explanation=(
        "Which fair? A reader seeing this fact alone cannot identify the event. "
        'Write "...at the 1893 World\'s Columbian Exposition in Chicago" instead.'
    ),
)

BAD_DANGLING_REFERENCE = ExtractionExample(
    text="The technique reduced error rates by 40% compared to the previous method.",
    is_good=False,
    explanation=(
        "Which technique? Which previous method? Name both explicitly: "
        '"Dropout regularization reduced neural network error rates by 40% compared '
        'to L2 weight decay in Srivastava et al. (2014)."'
    ),
)

# ── GOOD examples ────────────────────────────────────────────────────

GOOD_INVENTION = ExtractionExample(
    text=(
        "Kumar Patel at Bell Labs invented the carbon dioxide laser in 1964, "
        "which operates at a wavelength of 10.6 micrometers."
    ),
    is_good=True,
    explanation="Subject (Kumar Patel), predicate (invented), concrete claim with specifics.",
)

GOOD_DEMONSTRATION = ExtractionExample(
    text=(
        "Scientists at Arizona State University demonstrated the first white laser "
        "in 2015 by combining red, green, and blue semiconductor laser beams on a single chip."
    ),
    is_good=True,
    explanation="Subject (scientists at ASU), predicate (demonstrated), specific result with method.",
)

GOOD_DESCRIPTION = ExtractionExample(
    text=(
        "Marlan Scully's students described him as combining rigorous mathematical "
        "training with an intuitive approach to physics problems."
    ),
    is_good=True,
    explanation="Subject (students), predicate (described), specific observation about a person.",
)

GOOD_MEASUREMENT = ExtractionExample(
    text=(
        "Water purification using graphene oxide membranes achieves 99.9% removal "
        "of bacterial contaminants according to a 2021 study by MIT researchers."
    ),
    is_good=True,
    explanation="Subject (purification method), predicate (achieves), measurable claim with attribution.",
)

GOOD_PERSPECTIVE = ExtractionExample(
    text=(
        "The Electronic Frontier Foundation argues that end-to-end encryption must "
        "remain legally protected because any government backdoor creates a vulnerability "
        "exploitable by all adversaries, not just law enforcement."
    ),
    is_good=True,
    explanation=(
        "Clear stance holder (EFF), explicit position (encryption must remain protected), "
        "reasoning included. This is a perspective, not a neutral claim."
    ),
)

GOOD_QUOTE = ExtractionExample(
    text=(
        'Richard Feynman stated in his 1965 Nobel lecture: "The electron does anything '
        "it likes. It just goes in any direction at any speed, forward or backward in "
        'time, however it likes."'
    ),
    is_good=True,
    explanation="Verbatim quote with speaker, occasion, and date. Preserved exactly.",
)

GOOD_PROCEDURE = ExtractionExample(
    text=(
        "The Sanger DNA sequencing method proceeds in four steps: (1) denature the "
        "double-stranded DNA template, (2) anneal a primer to the single-stranded template, "
        "(3) extend the primer using DNA polymerase with chain-terminating dideoxynucleotides, "
        "(4) separate the resulting fragments by gel electrophoresis to read the sequence."
    ),
    is_good=True,
    explanation="Named procedure (Sanger method), ordered steps preserved as a complete unit.",
)

# ── Test instruction (included after examples in the prompt) ─────────

TEST_BEFORE_EXTRACTING = (
    "**Test before extracting**: Read the candidate fact in isolation — imagine it printed "
    "on an index card with NO other context. If a reader would ask "
    '"what is this about?", "which one?", or "where?" '
    "— the fact is not self-contained. Resolve every implicit reference using information "
    "from the source text (name the event, place, person, method, etc.), or skip the fact entirely."
)

# ── Registries ───────────────────────────────────────────────────────

ALL_TEXT_EXAMPLES: tuple[ExtractionExample, ...] = (
    BAD_TITLE,
    BAD_HEADLINE,
    BAD_FRAGMENT,
    BAD_NOUN_PHRASE,
    BAD_VAGUE_SUBJECT,
    BAD_VAGUE_LABEL,
    BAD_TOPIC_DESCRIPTION,
    BAD_MISSING_CONTEXT,
    BAD_DANGLING_REFERENCE,
    GOOD_INVENTION,
    GOOD_DEMONSTRATION,
    GOOD_DESCRIPTION,
    GOOD_MEASUREMENT,
    GOOD_PERSPECTIVE,
    GOOD_QUOTE,
    GOOD_PROCEDURE,
)

ALL_IMAGE_EXAMPLES: tuple[ExtractionExample, ...] = ()
