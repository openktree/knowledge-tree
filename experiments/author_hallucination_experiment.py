"""Experiment: Fix author-name hallucination via improved prompt.

Compares the current (hallucinating) prompt against an improved prompt
on the same inputs that produced fake "van der Heijden" variants in prod.

Run:
    uv run --project libs/kt-facts python experiments/author_hallucination_experiment.py

Requires: OPENROUTER_API_KEY in .env
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from kt_models.gateway import ModelGateway
from kt_facts.author import (
    LlmHeaderStrategy,
    SourceContext,
    _LLM_SYSTEM_PROMPT,
)
from kt_facts.processing.entity_extraction import _is_valid_entity_name


# ── Current (hallucinating) prompt ──────────────────────────────────────

OLD_PROMPT = _LLM_SYSTEM_PROMPT

# ── Improved prompt ─────────────────────────────────────────────────────

NEW_PROMPT = """\
Extract the author(s) and publishing organization from the provided information.

You will receive:
- The URL of the page
- The first ~500 characters of the extracted article text
- Optionally, structured metadata extracted from the page's HTML meta tags

Return a JSON object with two fields:
- "person": the individual author(s) who wrote this content, comma-separated if \
multiple. null if not identifiable.
- "organization": the publishing entity (newspaper, university, company, website \
name). null if not identifiable.

Rules:
- "person" = individual human authors only (journalists, researchers, bloggers)
- "organization" = the publisher, institution, or platform (BBC, Nature, arXiv, \
Google Brain, Wikipedia)
- For collaborative platforms (Wikipedia, Reddit, Stack Overflow): person is null, \
organization is the platform name
- If genuinely unidentifiable, return null for that field

CRITICAL — Author names must be EXPLICITLY VISIBLE in the provided text or metadata. \
Do NOT infer, guess, or reconstruct author names from:
- URLs, DOIs, or citation references
- Partial initials or abbreviations you "recognise"
- Your training data or general knowledge about who wrote a paper
- The abstract or body text of an academic article (these rarely contain author names)

If the text you receive is an abstract, methodology section, or any content that \
does not explicitly state "by [name]" or "Author: [name]", return null for person. \
Academic paper abstracts almost never contain author names — do not hallucinate them.

When in doubt, return null. A missing author is far better than a wrong one.

Return ONLY the JSON object. No markdown fences."""


# ── Test data ───────────────────────────────────────────────────────────

SOURCES = [
    {
        "label": "Nature paper (placebo effects)",
        "url": "https://www.nature.com/articles/s41380-024-02638-x",
        "header": (
            "Abstract\n"
            "There is a growing literature exploring the placebo response "
            "within specific mental disorders, but no overarching quantitative "
            "synthesis of this research has analyzed evidence across mental "
            "disorders. We carried out an umbrella review of meta-analyses of "
            "randomized controlled trials (RCTs) of biological treatments "
            "(pharmacotherapy or neurostimulation) for mental disorders. We "
            "explored whether placebo effect size differs across distinct "
            "disorders, and the correlates of increased placebo effects. Based "
            "on a pre-registered protocol, we searched Medline, PsycInfo, "
            "EMBASE, and Web of Knowl"
        ),
        "db_hallucination": "M. A. M. van der Heijden, M. J. H. M. van der Heijden, A. M. J. M. van der Heijden",
        "html_metadata": None,
    },
    {
        "label": "ResearchGate (storytelling intervention)",
        "url": "https://www.researchgate.net/publication/332229773_The_Psychosocial_Impact_and_Value_of_Participating_in_a_Storytelling_Intervention_for_Patients_Diagnosed_with_Cancer_An_Integrative_Review",
        "header": (
            "Aims To undertake an integrative review of evidence identifying "
            "the impact and outcomes from storytelling interventions for people "
            "with cancer."
        ),
        "db_hallucination": "S. J. M. M. van der Heijden, M. J. Schouten, M. J. M. van der Heijden, M. J. Schouten, M. J. M. van der Heijden, M. J. Schouten",
        "html_metadata": None,
    },
    {
        "label": "SCIRP (cancer experiences)",
        "url": "https://www.scirp.org/journal/paperinformation?paperid=92369",
        "header": (
            "Discover the impact of storytelling interventions for people "
            "with cancer. Explore 11 studies and gain unique insights into "
            "psycho-emotional outcomes."
        ),
        "db_hallucination": "M. A. M. van der Heijden, M. J. M. van der Heijden, M. J",
        "html_metadata": None,
    },
    # Control: a source where author IS visible in the header
    {
        "label": "Control — author visible in header",
        "url": "https://example.com/blog/placebo-myths",
        "header": (
            "Placebo Myths Debunked\n"
            "By Sarah Chen, PhD | Published March 2024\n\n"
            "The placebo effect is one of the most misunderstood phenomena "
            "in medicine. In this article, we examine five common myths..."
        ),
        "db_hallucination": None,
        "html_metadata": None,
    },
    # Control: metadata has author
    {
        "label": "Control — author in HTML metadata",
        "url": "https://example.com/research/review",
        "header": (
            "Abstract\n"
            "We conducted a systematic review of 30 studies examining "
            "the relationship between sleep quality and cognitive performance "
            "in older adults."
        ),
        "db_hallucination": None,
        "html_metadata": {"author": "James Rodriguez, Maria Lopez", "sitename": "Sleep Research Journal"},
    },
]


def _sep(title: str) -> None:
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")


async def _run_extraction(
    gateway: ModelGateway,
    system_prompt: str,
    source: dict,
) -> str | None:
    """Run author extraction with a given system prompt."""
    ctx = SourceContext(
        url=source["url"],
        header_text=source["header"],
        html_metadata=source.get("html_metadata"),
    )

    # Manually invoke the LLM with our chosen system prompt
    # (instead of using LlmHeaderStrategy which hardcodes the prompt)
    from kt_facts.author import (
        _LLM_USER_TEMPLATE,
        _LLM_USER_TEMPLATE_WITH_META,
        _clean_llm_field,
    )

    header_text = ctx.header_text[:500] if ctx.header_text.strip() else "(no content available)"

    if ctx.html_metadata:
        meta_lines = "\n".join(
            f"  {k}: {v}" for k, v in ctx.html_metadata.items() if v
        )
        user_msg = _LLM_USER_TEMPLATE_WITH_META.format(
            url=ctx.url,
            header=header_text,
            metadata=meta_lines if meta_lines else "(empty)",
        )
    else:
        user_msg = _LLM_USER_TEMPLATE.format(
            url=ctx.url,
            header=header_text,
        )

    try:
        result = await gateway.generate_json(
            model_id=gateway.decomposition_model,
            messages=[{"role": "user", "content": user_msg}],
            system_prompt=system_prompt,
            temperature=0.0,
            max_tokens=200,
        )
        if not result or not isinstance(result, dict):
            return None
        person = _clean_llm_field(result.get("person"))
        return person
    except Exception as e:
        print(f"    ERROR: {e}")
        return None


def _analyze_person(person: str | None) -> tuple[list[str], bool]:
    """Split person string into names and check for hallucination signals."""
    if not person:
        return [], False
    names = [n.strip() for n in person.split(",") if n.strip()]
    has_hallucination = False
    for name in names:
        tokens = name.replace(".", "").split()
        # Flag if 3+ single-letter tokens (academic initials pattern)
        initial_count = sum(1 for t in tokens if len(t) == 1 and t.isalpha())
        if initial_count >= 3:
            has_hallucination = True
    return names, has_hallucination


async def main():
    _sep("AUTHOR HALLUCINATION FIX EXPERIMENT")
    print("Comparing old prompt (hallucinating) vs improved prompt")
    print("on the same sources that produced fake 'van der Heijden' nodes.\n")

    gateway = ModelGateway()
    print(f"Model: {gateway.decomposition_model}\n")

    print("Current prompt excerpt:")
    print(f"  ...{OLD_PROMPT[200:350]}...\n")
    print("New prompt adds:")
    print("  - CRITICAL: names must be EXPLICITLY VISIBLE in text/metadata")
    print("  - Academic abstracts almost never contain author names")
    print("  - When in doubt, return null")

    # ── Run both prompts on all sources ─────────────────────────────
    _sep("RESULTS")

    old_hallucinations = 0
    new_hallucinations = 0
    old_correct_nulls = 0
    new_correct_nulls = 0
    old_correct_extractions = 0
    new_correct_extractions = 0

    for source in SOURCES:
        print(f"--- {source['label']} ---")
        print(f"URL: {source['url']}")
        print(f"Header: {source['header'][:100]}...")
        if source.get("html_metadata"):
            print(f"Metadata: {source['html_metadata']}")
        print()

        old_person = await _run_extraction(gateway, OLD_PROMPT, source)
        new_person = await _run_extraction(gateway, NEW_PROMPT, source)

        old_names, old_has_halluc = _analyze_person(old_person)
        new_names, new_has_halluc = _analyze_person(new_person)

        is_control = source["db_hallucination"] is None

        print(f"  Old prompt: {old_person or '(null)'}")
        if old_names:
            for name in old_names:
                valid = _is_valid_entity_name(name)
                print(f"    -> '{name}' valid={valid}")

        print(f"  New prompt: {new_person or '(null)'}")
        if new_names:
            for name in new_names:
                valid = _is_valid_entity_name(name)
                print(f"    -> '{name}' valid={valid}")

        # Score results
        if is_control:
            # Control: we WANT author extraction to work
            if old_person:
                old_correct_extractions += 1
                print(f"  Old: CORRECT (extracted real author)")
            else:
                print(f"  Old: MISSED (should have found author)")
            if new_person:
                new_correct_extractions += 1
                print(f"  New: CORRECT (extracted real author)")
            else:
                print(f"  New: MISSED (should have found author)")
        else:
            # Hallucination source: we WANT null
            if old_person is None:
                old_correct_nulls += 1
                print(f"  Old: CORRECT NULL")
            elif old_has_halluc:
                old_hallucinations += 1
                print(f"  Old: HALLUCINATED (3+ initials detected)")
            else:
                print(f"  Old: RETURNED SOMETHING (may be wrong)")

            if new_person is None:
                new_correct_nulls += 1
                print(f"  New: CORRECT NULL")
            elif new_has_halluc:
                new_hallucinations += 1
                print(f"  New: STILL HALLUCINATING")
            else:
                print(f"  New: RETURNED SOMETHING (may be wrong)")

        print()

    # ── Summary ─────────────────────────────────────────────────────
    _sep("SUMMARY")
    halluc_sources = sum(1 for s in SOURCES if s["db_hallucination"] is not None)
    control_sources = sum(1 for s in SOURCES if s["db_hallucination"] is None)

    print(f"Hallucination sources ({halluc_sources} total):")
    print(f"  Old prompt: {old_hallucinations} hallucinated, {old_correct_nulls} correct nulls")
    print(f"  New prompt: {new_hallucinations} hallucinated, {new_correct_nulls} correct nulls")
    print()
    print(f"Control sources ({control_sources} total — author IS visible):")
    print(f"  Old prompt: {old_correct_extractions} correctly extracted")
    print(f"  New prompt: {new_correct_extractions} correctly extracted")
    print()

    if new_hallucinations == 0 and new_correct_extractions == control_sources:
        print("SUCCESS: New prompt eliminates hallucinations while preserving")
        print("correct extraction when authors are actually visible.")
    elif new_hallucinations == 0:
        print("PARTIAL: New prompt eliminates hallucinations but may be too")
        print("aggressive (missed some legitimate authors in controls).")
    elif new_hallucinations < old_hallucinations:
        print("IMPROVED: New prompt reduces hallucinations but doesn't fully")
        print("eliminate them. May need a smarter model.")
    else:
        print("NO IMPROVEMENT: New prompt did not reduce hallucinations.")
        print("This model may be too weak — consider upgrading.")


if __name__ == "__main__":
    asyncio.run(main())
