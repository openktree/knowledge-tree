"""Big-seed v2 data model — flat container by default, paths only on disambig."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Literal

DecisionKind = Literal[
    "genesis",                       # no candidates → new flat big-seed
    "alias_hit",                     # bookkeeping-only: reverse alias lookup surfaced candidate(s)
    "merge_into_big_seed",           # incoming merged into flat big-seed
    "merge_into_path",               # big-seed already split; merged into existing disambig path
    "split_big_seed",                # flat big-seed split into disambig paths
    "new_disambig_path",             # big-seed already split; incoming becomes new disambig branch
    "shell",                         # shell_classify marked as shell noun — short-circuited
    "merge_by_exact_extraction",     # Phase B: same literal name seen in another fact
    "merge_by_alias_match",          # Phase D: name is a verbatim alias of another unique name
]


@dataclass
class Usage:
    kind: str
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
    id: str
    content: str
    source: str = ""


@dataclass
class NamedVec:
    """An embedded surface form. source_name tells you which alias this vec
    belongs to; one big-seed/path may hold many (one per alias + canonical)."""

    source_name: str
    vec: list[float]


@dataclass
class Path:
    """Disambiguation branch within a big-seed. Exists only when big-seed
    is ambiguous. Label must be unambiguous, e.g. "John (actor)"."""

    id: str
    label: str
    aliases: list[str] = field(default_factory=list)
    embeddings: list[NamedVec] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)

    @staticmethod
    def new(label: str) -> Path:
        return Path(id=uuid.uuid4().hex[:8], label=label)


@dataclass
class BigSeed:
    """Flat container by default. Holds many aliases + multiple embeddings.
    `paths` populated only after disambiguation."""

    id: str
    canonical_name: str
    node_type: str
    aliases: list[str] = field(default_factory=list)
    embeddings: list[NamedVec] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    paths: list[Path] = field(default_factory=list)
    alias_gen_usage: Usage | None = None
    alias_gen_response: dict | None = None

    @property
    def ambiguous(self) -> bool:
        return bool(self.paths)

    @staticmethod
    def new(canonical: str, node_type: str) -> BigSeed:
        return BigSeed(id=uuid.uuid4().hex[:8], canonical_name=canonical, node_type=node_type)

    def find_path(self, path_id: str) -> Path | None:
        for p in self.paths:
            if p.id == path_id:
                return p
        return None


@dataclass
class Candidate:
    """A (big_seed, optional path) candidate surfaced by reverse-alias or
    qdrant search. `via` is "alias" | "embedding" | "both".
    score only meaningful for embedding hits."""

    big_seed_id: str
    path_id: str | None
    canonical_name: str
    path_label: str | None
    score: float
    via: str
    matched_alias: str | None = None       # which alias hit (reverse lookup)
    matched_source_name: str | None = None  # which embed source_name hit


@dataclass
class Decision:
    """One intake event — full observable trace per incoming seed."""

    step: int
    incoming_name: str
    incoming_fact_count: int
    incoming_fact_samples: list[str] = field(default_factory=list)
    incoming_aliases: list[str] = field(default_factory=list)

    alias_gen_usage: Usage | None = None
    alias_gen_response: dict | None = None

    # shell classifier (separate LLM call)
    shell_classification_usage: Usage | None = None
    shell_classification_response: dict | None = None

    # lookup results
    reverse_alias_hits: list[Candidate] = field(default_factory=list)
    embed_candidates: list[Candidate] = field(default_factory=list)
    all_embed_scores: list[tuple[str, float]] = field(default_factory=list)  # (label, score) sorted desc

    # multiplex
    multiplex_usage: Usage | None = None
    multiplex_response: dict | None = None

    # outcome
    kind: DecisionKind = "genesis"
    target_big_seed_id: str | None = None
    target_big_seed_canonical: str | None = None
    target_path_id: str | None = None
    target_path_label: str | None = None
    split_paths: list[dict] = field(default_factory=list)   # only for split_big_seed
    disambig_label: str = ""
    reason: str = ""


@dataclass
class ShellSeed:
    """A candidate classified by alias_gen as a shell noun — short-circuits
    the pipeline: never embedded, never sent to multiplex, never promoted."""

    name: str
    node_type: str
    fact_count: int
    fact_samples: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    reason: str = ""
    alias_gen_usage: Usage | None = None


@dataclass
class Registry:
    """Global pool — big-seeds, alias index, qdrant handle wrapper.

    alias_index: lowercased alias → list of (big_seed_id, path_id|None).
      path_id=None means the alias lives at the big-seed's flat level.
    history: ordered list of Decision, one per intake.
    shell_seeds: candidates the alias_gen stage marked as shell nouns;
      never embedded, never merged, never promoted.
    """

    big_seeds: list[BigSeed] = field(default_factory=list)
    alias_index: dict[str, list[tuple[str, str | None]]] = field(default_factory=dict)
    history: list[Decision] = field(default_factory=list)
    shell_seeds: list[ShellSeed] = field(default_factory=list)

    def find_big_seed(self, bs_id: str) -> BigSeed | None:
        for b in self.big_seeds:
            if b.id == bs_id:
                return b
        return None

    def register_alias(self, alias: str, bs_id: str, path_id: str | None) -> None:
        key = alias.strip().lower()
        if not key:
            return
        bucket = self.alias_index.setdefault(key, [])
        if (bs_id, path_id) not in bucket:
            bucket.append((bs_id, path_id))

    def lookup_aliases(self, names: list[str]) -> list[tuple[str, str, str | None]]:
        """For each candidate name, return (matched_alias, bs_id, path_id)."""
        hits: list[tuple[str, str, str | None]] = []
        seen: set[tuple[str, str | None]] = set()
        for n in names:
            key = n.strip().lower()
            for bs_id, path_id in self.alias_index.get(key, []):
                if (bs_id, path_id) in seen:
                    continue
                seen.add((bs_id, path_id))
                hits.append((n, bs_id, path_id))
        return hits
