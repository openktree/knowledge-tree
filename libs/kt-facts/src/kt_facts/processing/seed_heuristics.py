"""Pure seed heuristic functions — zero async, zero DB, zero kt_db imports.

Extracted from seed_dedup.py and seed_routing.py so that experiment scripts
and tests can import the same logic that runs in production.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

# ── Constants ────────────────────────────────────────────────────────────

# Words that carry little distinguishing meaning — safe to ignore in diffs.
STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "of",
        "for",
        "in",
        "on",
        "to",
        "and",
        "or",
        "at",
        "by",
        "is",
        "it",
        "its",
        "s",
        "as",
    }
)

# Words to skip when extracting initials from an expanded name.
ACRONYM_SKIP_WORDS = frozenset(
    {
        "of",
        "the",
        "and",
        "for",
        "in",
        "on",
        "to",
        "a",
        "an",
        "at",
        "by",
    }
)

# Pattern for tokens that look like dates or numbers — strong distinguishers.
NUMBER_RE = re.compile(r"\d")


# ── Data stubs ───────────────────────────────────────────────────────────


@dataclass
class SeedStub:
    """Lightweight stand-in for WriteSeed — usable without DB."""

    key: str
    name: str
    node_type: str
    status: str = "active"
    fact_count: int = 1
    merged_into_key: str | None = None
    promoted_node_key: str | None = None
    metadata_: dict | None = None
    context_hash: str | None = None
    entity_subtype: str | None = None
    phonetic_code: str | None = None
    seed_uuid: str | None = None
    aliases: list = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.aliases is None:
            self.aliases = []


@dataclass
class RouteStub:
    """Lightweight stand-in for WriteSeedRoute."""

    parent_seed_key: str
    child_seed_key: str
    label: str
    ambiguity_type: str = "text"


@dataclass
class QdrantMatchStub:
    """Lightweight stand-in for Qdrant search result."""

    seed_key: str
    score: float


# ── Pure heuristic functions ─────────────────────────────────────────────


def is_distinguishing_word(word: str) -> bool:
    """Return True if *word* is a meaningful distinguishing token.

    Distinguishing words are ones whose presence/absence or difference
    indicates a genuinely different entity.  Articles, prepositions, and
    other stopwords are NOT distinguishing.
    """
    if word in STOPWORDS:
        return False
    # Single-char tokens only count if they contain a digit (e.g. "1", "2")
    if len(word) <= 1:
        return bool(NUMBER_RE.search(word))
    # Initials like "k.", "a.", "j.f.k." are abbreviations, not distinguishing
    if "." in word:
        stripped = word.replace(".", "")
        if len(stripped) <= 2 and not NUMBER_RE.search(stripped):
            return False
    return True


def is_acronym_match(name_a: str, name_b: str) -> bool:
    """Check if one name is an acronym of the other.

    Returns True when one string is a short all-caps token (2-6 chars) whose
    letters match the first letters of the significant words in the other
    string.

    Examples:
        is_acronym_match("FBI", "Federal Bureau of Investigation") → True
        is_acronym_match("NASA", "National Aeronautics and Space Administration") → True
        is_acronym_match("J.F.K.", "John F. Kennedy") → True
        is_acronym_match("FBI", "CIA") → False
    """
    a_stripped = name_a.strip()
    b_stripped = name_b.strip()

    # Normalise dotted acronyms: "J.F.K." → "JFK", "U.S.A." → "USA"
    a_nodots = a_stripped.replace(".", "").replace(" ", "")
    b_nodots = b_stripped.replace(".", "").replace(" ", "")

    # Determine which is the acronym and which is the expansion
    acronym: str | None = None
    expansion: str | None = None

    if a_nodots.isupper() and 2 <= len(a_nodots) <= 6 and len(b_stripped.split()) >= 2:
        acronym, expansion = a_nodots, b_stripped
    elif b_nodots.isupper() and 2 <= len(b_nodots) <= 6 and len(a_stripped.split()) >= 2:
        acronym, expansion = b_nodots, a_stripped
    else:
        return False

    # Extract first letters of significant words from the expansion
    words = expansion.split()
    initials = [w[0].upper() for w in words if w.lower() not in ACRONYM_SKIP_WORDS and w]

    if not initials:
        return False

    initials_str = "".join(initials)

    # Direct match: "FBI" == "FBI" from "Federal Bureau of Investigation"
    if acronym == initials_str:
        return True

    # Relaxed: allow skipping one word (handles edge cases like
    # "United Nations Children's Fund" → "UNICEF" where 'C' comes from Children's)
    # Try matching acronym chars against initials in order, allowing skips
    if len(acronym) >= 2 and len(initials) >= len(acronym):
        i = 0  # pointer into acronym
        for ch in initials:
            if i < len(acronym) and ch == acronym[i]:
                i += 1
        if i == len(acronym):
            return True

    return False


def is_containment_mismatch(name_a: str, name_b: str) -> bool:
    """Check if two names should NOT be merged despite high trigram similarity.

    Returns True (= don't merge) in two cases:

    1. **Containment** — one name's word-set is a subset/substring of the
       other, but the larger name has significant extra words (e.g. "Jeffrey
       Epstein" vs "Jeffrey Epstein's Lawyer").

    2. **Distinguishing-word swap** — both names share most words but each
       side has at least one meaningful word the other lacks (e.g.
       "…New York" vs "…Florida", "World War 1" vs "World War 2",
       "2006 Arrest" vs "July 6 2019 Arrest").
    """
    words_a = set(name_a.split())
    words_b = set(name_b.split())

    # Identical word sets → same entity
    if words_a == words_b:
        return False

    only_a = words_a - words_b
    only_b = words_b - words_a

    # ── Case 1: one side is a subset (containment) ──────────────────
    if not only_a or not only_b:
        # One is a subset of the other
        extra = only_a or only_b
        meaningful_extra = [w for w in extra if is_distinguishing_word(w)]
        if len(meaningful_extra) >= 2:
            return True
        if meaningful_extra and len(meaningful_extra[0]) >= 4:
            return True
        # If all extra words are stopwords, don't block — it's likely
        # the same entity with an article/preposition difference.
        if not meaningful_extra:
            return False
        # Also check raw substring containment with length ratio
        if name_a in name_b or name_b in name_a:
            shorter_name = name_a if len(name_a) < len(name_b) else name_b
            longer_name = name_b if len(name_a) < len(name_b) else name_a
            extra_chars = len(longer_name) - len(shorter_name)
            if extra_chars > 0.3 * len(shorter_name):
                return True
        return False

    # ── Case 2: distinguishing-word swap ────────────────────────────
    # Both sides have unique words.  If either side has a meaningful
    # distinguishing word, the names likely refer to different entities.
    shared = words_a & words_b
    total_unique = len(words_a | words_b)
    shared_ratio = len(shared) / total_unique if total_unique else 0.0

    if shared_ratio >= 0.4:
        dist_a = [w for w in only_a if is_distinguishing_word(w)]
        dist_b = [w for w in only_b if is_distinguishing_word(w)]

        if dist_a and dist_b:
            return True

    # Lower bar for short names (2-3 words each)
    if shared and shared_ratio >= 0.25 and len(words_a) <= 3 and len(words_b) <= 3:
        dist_a = [w for w in only_a if is_distinguishing_word(w)]
        dist_b = [w for w in only_b if is_distinguishing_word(w)]

        if dist_a and dist_b:
            # Check the distinguishing words aren't just typos/abbreviations
            all_typo = True
            for da in dist_a:
                for db in dist_b:
                    if da in db or db in da:
                        continue
                    ed = edit_distance(da, db)
                    max_len = max(len(da), len(db))
                    if max_len > 0 and ed / max_len > 0.25:
                        all_typo = False
                        break
                if not all_typo:
                    break
            if not all_typo:
                return True

    # One-sided extra with a substantial word
    if name_a in name_b or name_b in name_a:
        shorter_name = name_a if len(name_a) < len(name_b) else name_b
        longer_name = name_b if len(name_a) < len(name_b) else name_a
        extra_chars = len(longer_name) - len(shorter_name)
        if extra_chars > 0.3 * len(shorter_name):
            return True

    return False


def edit_distance(a: str, b: str) -> int:
    """Levenshtein edit distance (dynamic programming)."""
    if len(a) < len(b):
        return edit_distance(b, a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1] + [0] * len(b)
        for j, cb in enumerate(b):
            curr[j + 1] = min(
                prev[j + 1] + 1,  # deletion
                curr[j] + 1,  # insertion
                prev[j] + (0 if ca == cb else 1),  # substitution
            )
        prev = curr
    return prev[len(b)]


def differs_only_by_digit_or_initial(name_a: str, name_b: str) -> bool:
    """True if two names differ only by a digit, roman numeral, or single initial.

    Catches pairs like:
        "APVAC1" vs "APVAC2"
        "Phase I trial" vs "Phase II trial"
        "Ana R. S. Silva" vs "Ana R. P. Silva"
        "ParvOryx01 protocol" vs "ParvOryx02 protocol"
    """
    a_l, b_l = name_a.lower().strip(), name_b.lower().strip()
    if a_l == b_l:
        return False

    tok_a = re.findall(r"[a-z]+|[0-9]+|[^a-z0-9\s]+", a_l)
    tok_b = re.findall(r"[a-z]+|[0-9]+|[^a-z0-9\s]+", b_l)

    if len(tok_a) != len(tok_b):
        return False

    diff_positions = [i for i, (ta, tb) in enumerate(zip(tok_a, tok_b)) if ta != tb]
    if not diff_positions or len(diff_positions) > 2:
        return False

    _roman = {"i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"}
    for pos in diff_positions:
        ta, tb = tok_a[pos], tok_b[pos]
        if ta.isdigit() and tb.isdigit():
            continue
        if ta in _roman and tb in _roman:
            continue
        if len(ta) <= 2 and len(tb) <= 2 and ta.isalpha() and tb.isalpha():
            continue
        return False
    return True


def has_academic_initials(name: str) -> bool:
    """True if name contains academic-style initials (e.g. 'J. P.', 'M. A.')."""
    return bool(re.search(r"\b[A-Z]\.\s", name))


def is_safe_auto_merge(
    name_a: str,
    name_b: str,
    embedding_score: float,
    auto_merge_threshold: float,
) -> bool:
    """Check if a pair can be safely auto-merged without LLM confirmation.

    Returns True only when embedding similarity is very high AND string-level
    guards confirm the names are not a dangerous pair (person name initials,
    numbered protocols, containment mismatches).
    """
    if embedding_score < auto_merge_threshold:
        return False
    if is_containment_mismatch(name_a.lower(), name_b.lower()):
        return False
    d = edit_distance(name_a.lower(), name_b.lower())
    max_len = max(len(name_a), len(name_b))
    str_sim = 1.0 - d / max_len if max_len else 1.0
    if str_sim < 0.65:
        return False
    if differs_only_by_digit_or_initial(name_a, name_b):
        return False
    if has_academic_initials(name_a) or has_academic_initials(name_b):
        return False
    return True


def is_prefix_disambiguation_candidate(name_a: str, name_b: str) -> bool:
    """Check if two names share a significant common prefix.

    Returns True if both names start identically up to a word boundary
    and then diverge with distinguishing words.

    Examples:
        "light-dependent reactions" vs "light-independent reactions" -> True
        "North Korea" vs "North Macedonia" -> True
        "light-dependent reactions" vs "dark reactions" -> False
    """
    a_lower = name_a.lower().strip()
    b_lower = name_b.lower().strip()

    if a_lower == b_lower:
        return False

    # Find common prefix length
    prefix_len = 0
    for i, (ca, cb) in enumerate(zip(a_lower, b_lower)):
        if ca != cb:
            break
        prefix_len = i + 1
    else:
        # One is a pure prefix of the other — containment, not disambiguation
        if len(a_lower) != len(b_lower):
            return False

    if prefix_len < 4:
        return False

    # Check that the prefix ends at a word/separator boundary
    prefix = a_lower[:prefix_len]
    if prefix[-1] not in (" ", "-", "_") and prefix_len < len(a_lower) and prefix_len < len(b_lower):
        # Back up to last word boundary
        for j in range(prefix_len - 1, 0, -1):
            if a_lower[j] in (" ", "-", "_"):
                prefix_len = j + 1
                break
        else:
            return False

    # Both must have distinguishing content after the prefix
    suffix_a = a_lower[prefix_len:].strip()
    suffix_b = b_lower[prefix_len:].strip()

    if not suffix_a or not suffix_b:
        return False  # One is a pure prefix of the other

    return True


# ── Utility functions (from seed_routing.py) ─────────────────────────────


def compute_phonetic_code(name: str) -> str:
    """Compute double metaphone code for a name."""
    from metaphone import doublemetaphone

    return doublemetaphone(name.lower().strip())[0] or ""


def build_seed_context(
    name: str,
    node_type: str,
    top_facts: list[str] | None = None,
    aliases: list[str] | None = None,
) -> str:
    """Build contextual text for seed embedding."""
    parts = [name, node_type]
    if aliases:
        parts.append(f"aliases: {', '.join(aliases[:5])}")
    if top_facts:
        parts.append("; ".join(f[:200] for f in top_facts[:3]))
    return " | ".join(parts)


def compute_context_hash(context_text: str) -> str:
    """Hash context text for staleness detection."""
    return hashlib.sha256(context_text.encode()).hexdigest()


def text_search_route(
    fact_content: str,
    routes: list,
) -> str | None:
    """Route by searching for child seed names in the fact text.

    For embedding ambiguity, the fact text usually contains the literal seed
    name (e.g., a fact about "light-dependent reactions" literally says
    "light-dependent"). Simple case-insensitive substring search.
    """
    fact_lower = fact_content.lower()
    matches = []
    for route in routes:
        if route.label.lower() in fact_lower:
            matches.append(route)

    if len(matches) == 1:
        return matches[0].child_seed_key

    # Multiple matches or zero — can't decide by text alone
    return None


# ── Trigram similarity ───────────────────────────────────────────────────


def trigram_similarity(a: str, b: str) -> float:
    """Approximate PostgreSQL pg_trgm similarity. Returns 0.0-1.0."""
    a_lower = a.lower().strip()
    b_lower = b.lower().strip()

    def trigrams(s: str) -> set[str]:
        padded = f"  {s} "
        return {padded[i : i + 3] for i in range(len(padded) - 2)}

    ta = trigrams(a_lower)
    tb = trigrams(b_lower)
    if not ta or not tb:
        return 0.0
    intersection = len(ta & tb)
    union = len(ta | tb)
    return intersection / union if union else 0.0


# ── Dedup decision logic ────────────────────────────────────────────────


@dataclass
class DedupSignals:
    """All heuristic signals for a single dedup candidate."""

    embedding_score: float = 0.0
    trigram_match: bool = False
    is_acronym: bool = False
    is_containment_block: bool = False
    is_prefix_disambig: bool = False
    phonetic_match: bool = False
    alias_exact_match: bool = False


@dataclass
class DedupDecision:
    """Result of the pure decision tree."""

    would_merge: bool
    signal: str  # "alias_exact", "acronym", "embedding", "phonetic_trigram", "none", etc.


def evaluate_dedup_signals(
    signals: DedupSignals,
    embed_threshold: float,
    typo_floor: float,
) -> DedupDecision:
    """Pure decision tree for single-candidate dedup. No IO.

    Encodes the same signal priority as deduplicate_seed():
    1. Alias/acronym (if trigram): merge unless containment blocks
    2. Embedding above threshold: merge unless prefix_disambig blocks
    3. Phonetic + trigram + above floor: merge unless containment blocks
    4. Otherwise: no merge
    """
    # ── Signal 0: Alias/acronym match (requires trigram candidate discovery) ──
    if signals.trigram_match:
        if signals.alias_exact_match or signals.is_acronym:
            # Containment guard (skip for acronyms)
            if signals.is_acronym or not signals.is_containment_block:
                signal = "acronym" if signals.is_acronym and not signals.alias_exact_match else "alias_exact"
                return DedupDecision(would_merge=True, signal=signal)

    # ── Signal 1: Embedding similarity (PRIMARY) ──
    if signals.embedding_score >= embed_threshold:
        if signals.is_prefix_disambig:
            return DedupDecision(would_merge=False, signal="embedding_blocked_by_prefix")
        return DedupDecision(would_merge=True, signal="embedding")

    # ── Signal 2: Phonetic + trigram typo catch ──
    if signals.embedding_score >= typo_floor and signals.phonetic_match and signals.trigram_match:
        if signals.is_containment_block:
            return DedupDecision(would_merge=False, signal="phonetic_blocked_by_containment")
        return DedupDecision(would_merge=True, signal="phonetic_trigram")

    # No signal fired
    return DedupDecision(would_merge=False, signal="none")
