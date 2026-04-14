"""Big-seed data model — in-memory only, no DB."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Literal

DecisionKind = Literal[
    "alias_match",          # exact alias hit on parent or path
    "embed_auto_route",     # embedding >= auto-route cutoff, routed to path
    "embed_reject",         # embedding below floor vs everything, rejected
    "llm_merge_path",       # LLM picked an existing path
    "llm_alias_to_parent",  # LLM said alias of canonical, not a path split
    "llm_new_path",         # LLM created a new disambiguation branch
    "llm_reject",           # LLM said different entity altogether
    "seed_init",            # the first member that seeded the big seed
]


@dataclass
class Usage:
    """Token + cost accounting for a single LLM call."""

    kind: str                    # "alias_gen" | "multiplex" | "embed"
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class Fact:
    """Minimal fact shape used by the experiment."""

    id: str
    content: str
    source: str = ""  # optional, for display


@dataclass
class Path:
    """A disambiguation branch within a big seed.

    Two kinds of aliases live here:
    - known_aliases: LLM-generated at birth from sample facts. Real-world
      alternative names for the SAME concept (acronyms, stylized spellings).
    - merged_surface_forms: incoming surface forms that were admitted into
      this path via alias_match / embed_auto_route / llm_merge_path. These
      are "embedding-ambiguous" — not necessarily the same as known
      aliases, but routed here by the multiplexer.
    """

    id: str
    label: str
    known_aliases: list[str] = field(default_factory=list)
    merged_surface_forms: list[str] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    embedding: list[float] | None = None
    alias_gen_usage: Usage | None = None
    alias_gen_response: dict | None = None  # raw LLM response (for report)

    @staticmethod
    def new(label: str) -> Path:
        return Path(id=uuid.uuid4().hex[:8], label=label)


@dataclass
class Decision:
    """One admit step in the replay trace."""

    step: int
    incoming_name: str
    incoming_fact_count: int
    incoming_fact_samples: list[str] = field(default_factory=list)  # shown in report
    kind: DecisionKind = "llm_reject"
    routed_to_path_id: str | None = None
    routed_to_path_label: str | None = None
    reason: str = ""
    embed_scores: dict[str, float] = field(default_factory=dict)  # path_label -> cosine
    best_embed_score: float = 0.0
    alias_gate: str = ""  # alias hit type if any
    multiplex_usage: Usage | None = None
    multiplex_response: dict | None = None  # raw LLM response
    alias_gen_usage: Usage | None = None
    alias_gen_response: dict | None = None


@dataclass
class BigSeed:
    """The new unified seed container."""

    canonical_name: str
    node_type: str
    merged_surface_forms: list[str] = field(default_factory=list)  # admitted via alias_to_parent
    paths: list[Path] = field(default_factory=list)
    history: list[Decision] = field(default_factory=list)

    def find_path(self, path_id: str) -> Path | None:
        for p in self.paths:
            if p.id == path_id:
                return p
        return None

    def total_usage(self) -> dict[str, Usage]:
        """Aggregate token usage across all decisions, split by kind."""
        totals: dict[str, Usage] = {
            "alias_gen": Usage(kind="alias_gen"),
            "multiplex": Usage(kind="multiplex"),
        }
        for p in self.paths:
            if p.alias_gen_usage:
                _acc(totals["alias_gen"], p.alias_gen_usage)
        for d in self.history:
            if d.multiplex_usage:
                _acc(totals["multiplex"], d.multiplex_usage)
        return totals


def _acc(dst: Usage, src: Usage) -> None:
    dst.prompt_tokens += src.prompt_tokens
    dst.completion_tokens += src.completion_tokens
    dst.cost_usd += src.cost_usd
    dst.latency_ms += src.latency_ms
    if src.model and not dst.model:
        dst.model = src.model
