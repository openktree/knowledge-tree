"""Extraction constraints for the fact decomposition pipeline.

Each ExtractionConstraint captures a section of rules/guidance that was
previously hardcoded in the prompt string. Constraints are sorted by
priority (lower = earlier in prompt) when assembled by the prompt builder.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExtractionConstraint:
    """A single constraint section for the extraction prompt."""

    heading: str
    body: str
    priority: int = 0

    def render(self) -> str:
        """Render as a markdown section for inclusion in a prompt."""
        return f"## {self.heading}\n\n{self.body}"


# ── Text extraction constraints ──────────────────────────────────────

EXTRACTION_RULES = ExtractionConstraint(
    heading="Rules",
    body=(
        "- Extract ONLY information explicitly stated in the text. Never add knowledge from outside.\n"
        '- For types marked "(1-3 sentences)", keep each fact atomic and self-contained.\n'
        '- For types marked "(can be multi-sentence/step/line)", preserve the full structure. '
        "Do NOT shred algorithms into individual steps or poems into individual lines. "
        "Capture the complete unit (up to ~500 words per fact).\n"
        "- Capture BOTH atomic facts (short claims, measurements) AND structured knowledge "
        "(code snippets, algorithms, detailed descriptions, quotes). Do not skip larger knowledge "
        "units just because they are long.\n"
        "- Extract attribution (who said it, where it was published, when, and brief context). "
        "Use null for any attribution field not present in the text.\n"
        "- All facts should contain all relevant subjects no fact should be ambiguos about what or who "
        "it is about"
    ),
    priority=5,
)

FACT_STRUCTURE = ExtractionConstraint(
    heading="Structure of a fact",
    body=(
        "A fact is a **complete assertion** \u2014 it must contain at minimum:\n"
        "- A **subject** \u2014 WHO or WHAT is this about (a named entity, concept, or thing)\n"
        "- A **predicate** \u2014 what the subject DOES, IS, or HAS (a verb or verb phrase)\n"
        "- A **claim or observation** \u2014 what is being asserted, measured, described, or quoted\n\n"
        "A string that lacks any of these is not a fact. Titles, labels, headings, and bare noun "
        "phrases fail this test."
    ),
    priority=10,
)

SELF_CONTAINMENT = ExtractionConstraint(
    heading="CRITICAL: Every fact must be self-contained",
    body=(
        'Each fact\'s "content" field will be stored in a knowledge graph and read WITHOUT the '
        "original source text. A reader seeing ONLY the fact must fully understand it.\n\n"
        '**Resolve all references.** Replace every pronoun, demonstrative ("this", "that", "these", '
        '"he", "shey", "we", "they"), '
        "and implicit subject with the explicit entity, person, concept, or topic name. The source "
        'text tells you who "he", "she", "they", "it", "the company", "the study", etc. refer to \u2014 '
        "substitute the actual name.\n\n"
        "**Name the subject.** Every fact must explicitly state WHAT or WHO it is about. "
        'Never write "It was founded in 1998" \u2014 write "Google was founded in 1998." '
        'Never write "The algorithm runs in O(n log n)" \u2014 write "Merge sort runs in O(n log n)." '
        'Never write "He proposed the theory" \u2014 write "Albert Einstein proposed the theory of '
        'general relativity."\n\n'
        "**Include the topic.** If a fact describes a property, event, or relationship, the fact "
        'must name both the subject and what it relates to. "The success rate was 94%" is useless \u2014 '
        '"The success rate of laparoscopic cholecystectomy was 94% in a 2019 meta-analysis" is '
        "a self-contained fact.\n\n"
        '**Name specific events, places, and contexts.** "At the fair" is meaningless without '
        'knowing WHICH fair \u2014 write "at the 1893 World\'s Columbian Exposition in Chicago". '
        '"The technique improved performance" is useless \u2014 name the technique and what it improved. '
        "Every proper noun, named event, specific method, or location that the source text provides "
        "must appear in the fact. If the source doesn't name it, the fact cannot reference it.\n\n"
        '**Reject incomplete fragments.** Hedging language ("may have had", "was allegedly", '
        '"has been linked to", "is reportedly") without a explicit obvious subject. For such strings '
        'either resolve who is he by saying "X may have had" , "Y was allegedly". Facts with open '
        "subjects should be rejected\n\n"
        "**Discard unresolvable facts.** If the source text does not provide enough context to "
        "determine what a pronoun or vague reference points to, DO NOT extract that fact. "
        "A fact that cannot be understood on its own is worthless in the knowledge graph \u2014 skip it "
        "rather than store an ambiguous statement."
    ),
    priority=20,
)

SKIP_RULES = ExtractionConstraint(
    heading="What to SKIP \u2014 do NOT extract these",
    body=(
        "The source text comes from web pages and may contain noise that is NOT knowledge about "
        "the topic. Discard the following:\n\n"
        "- **Platform metrics** \u2014 Upvote/downvote counts, like counts, share counts, view counts, "
        "comment counts, follower counts, star ratings of the page/post itself, karma scores. "
        "These are metadata about the container, not knowledge about the topic.\n"
        '- **Navigation and UI chrome** \u2014 "Click here", "Read more", "Subscribe", breadcrumb trails, '
        "menu items, sidebar content, footer boilerplate, cookie notices.\n"
        '- **Ephemeral page metadata** \u2014 "Last updated 2 hours ago", "Posted by user123", '
        '"5 min read", page word counts, reading time estimates.\n'
        '- **Advertising and promotion** \u2014 Calls to action, coupon codes, "Buy now", affiliate '
        "disclaimers, sponsored content labels.\n"
        '- **Self-referential framing** \u2014 "In this article we will discuss...", '
        '"This post covers...", "Let\'s dive in", "Thanks for reading". These describe the '
        "container, not the subject.\n"
        '- **Search engine / aggregator artifacts** \u2014 Truncation markers ("..."), "Showing results '
        'for", "Related searches", "People also ask" headings without answers.\n'
        '- **Empty, placeholder, or stub content** \u2014 Page numbers alone ("Page 495"), blank pages, '
        '"this page intentionally left blank", table of contents entries without content, chapter '
        "headings without body text, OCR artifacts with no readable text, or any text that merely "
        "references content without actually containing it. If the source text has no substantive "
        'information to extract, return {{"facts": []}}.\n'
        "- **Titles, headings, and labels** \u2014 Article titles, section headings, figure captions, "
        "and standalone labels are structural elements of the source document. They name a topic "
        "but assert nothing about it. Extract the substantive content these headings introduce, "
        "not the headings themselves.\n"
        '- **Incomplete fragments** \u2014 Bare noun phrases, prepositional phrases ("On knowing..."), '
        'and topic labels ("applications of X") are not facts. A fact must make an assertion that '
        "can be evaluated as true or false, informative or not.\n\n"
        "**Rule of thumb**: If a piece of information would become meaningless or misleading when "
        "separated from the specific web page it appeared on, it is not a fact \u2014 skip it. "
        "A fact should be about the TOPIC, not about the page."
    ),
    priority=30,
)

ALL_TEXT_CONSTRAINTS: tuple[ExtractionConstraint, ...] = (
    EXTRACTION_RULES,
    FACT_STRUCTURE,
    SELF_CONTAINMENT,
    SKIP_RULES,
)

# ── Image extraction constraints ─────────────────────────────────────

IMAGE_RULES = ExtractionConstraint(
    heading="Rules",
    body=(
        "- Extract ONLY information visible in the image. Never add knowledge from outside.\n"
        "- For charts/graphs: extract the data points, axis labels, trends, and conclusions.\n"
        "- For infographics: extract all facts, statistics, and relationships shown.\n"
        "- For photos: describe the subject, context, and any visible text.\n"
        "- For diagrams: extract the components, relationships, and labels.\n"
        "- Extract attribution (visible credits, watermarks, source labels). Use null if not visible.\n"
        "- If the image is blank, a placeholder, a page number, or contains no substantive visual "
        'information, return {{"facts": []}}.'
    ),
    priority=5,
)

IMAGE_SELF_CONTAINMENT = ExtractionConstraint(
    heading="CRITICAL: Every fact must be self-contained",
    body=(
        'Each fact\'s "content" field will be stored in a knowledge graph and read WITHOUT the '
        "original image. A reader seeing ONLY the fact must fully understand it.\n\n"
        '**Name every subject explicitly.** Never write "The chart shows growth" \u2014 write '
        '"The chart from [source] shows revenue growth of [Company] from $X to $Y between '
        '[year] and [year]." Use any visible labels, titles, captions, or watermarks to identify '
        "the subject.\n\n"
        "**Include full context.** If a diagram labels its components, name them in the fact. "
        "If a chart has axis labels, include them. If a photo shows a recognizable subject, "
        "name it.\n\n"
        "**Discard unidentifiable facts.** If the image does not provide enough context to "
        "determine what a visual element represents, DO NOT extract it. A fact that cannot be "
        "understood without seeing the original image is worthless \u2014 skip it."
    ),
    priority=20,
)

ALL_IMAGE_CONSTRAINTS: tuple[ExtractionConstraint, ...] = (
    IMAGE_RULES,
    IMAGE_SELF_CONTAINMENT,
)
