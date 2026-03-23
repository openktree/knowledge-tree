"""Quick test: does the new prompt still extract orgs from URLs?

Run:
    uv run --project libs/kt-facts python experiments/author_org_extraction_test.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from kt_models.gateway import ModelGateway
from kt_facts.author import LlmHeaderStrategy, SourceContext


SOURCES = [
    {
        "label": "CNBC article",
        "url": "https://www.cnbc.com/2024/03/15/markets-rally-on-fed-decision.html",
        "header": "Markets surged on Wednesday after the Federal Reserve held rates steady, signaling potential cuts later this year.",
    },
    {
        "label": "BBC article",
        "url": "https://www.bbc.com/news/science-environment-12345",
        "header": "Scientists have discovered a new species of deep-sea fish in the Mariana Trench, according to research published today.",
    },
    {
        "label": "Nature paper (no author visible)",
        "url": "https://www.nature.com/articles/s41380-024-02638-x",
        "header": "Abstract\nThere is a growing literature exploring the placebo response within specific mental disorders.",
    },
    {
        "label": "Reuters with byline",
        "url": "https://www.reuters.com/technology/ai-chips-2024",
        "header": "By Jane Smith\n\nNVIDIA announced record quarterly earnings driven by surging demand for AI chips.",
    },
    {
        "label": "Wikipedia",
        "url": "https://en.wikipedia.org/wiki/Placebo",
        "header": "A placebo is a substance or treatment which is designed to have no therapeutic value.",
    },
    {
        "label": "ArXiv paper",
        "url": "https://arxiv.org/abs/2401.12345",
        "header": "We present a novel approach to transformer architecture that reduces computational complexity from O(n^2) to O(n log n).",
    },
]


async def main():
    gateway = ModelGateway()
    strategy = LlmHeaderStrategy(gateway)
    print(f"Model: {gateway.decomposition_model}\n")

    for source in SOURCES:
        ctx = SourceContext(url=source["url"], header_text=source["header"])
        result = await strategy.extract(ctx)
        person = result.person if result else None
        org = result.organization if result else None
        print(f"[{source['label']}]")
        print(f"  person: {person}")
        print(f"  org:    {org}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
