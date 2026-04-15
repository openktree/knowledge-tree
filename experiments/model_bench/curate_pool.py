"""Auto-propose ground truth for the 100-item pool from cached model outputs.

Rules (conservative):
- alias_gen.aliases: include if emitted by >=2 models AND passes heuristic
  filters (not same as seed, not obvious specialization/coreference).
- alias_gen.must_exclude: empty by default (too risky to auto-blacklist).
- shell_classify.is_shell: only when >=4/6 models agree; else skip.
- suggest_disambig.ambiguous=True iff >=3/6 models emit >=2 paths;
  needles = case-insensitive tokens appearing in >=2 emissions.
- suggest_disambig.ambiguous=False iff >=4/6 emit <2 paths.

Writes proposals/pool_ground_truth.yaml for human review. Promote
approved entries into datasets.POOL_GROUND_TRUTH.

Usage:
    uv run --project services/api python -m experiments.model_bench.curate_pool
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import yaml


def _load_cache(cache_path: Path) -> dict[tuple[str, str, str], dict]:
    """Returns {(model, task, name): response_entry_dict}"""
    out: dict[tuple[str, str, str], dict] = {}
    for line in cache_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        task = r.get("task")
        names = r.get("names", [])
        resp = r.get("response")
        if r.get("error") or not resp:
            continue
        if not isinstance(resp, dict):
            continue
        for entry in resp.get("results", []) or []:
            if not isinstance(entry, dict):
                continue
            try:
                idx = int(entry.get("index"))
            except (TypeError, ValueError):
                continue
            if idx < 1 or idx > len(names):
                continue
            name = names[idx - 1]
            out[(r["model"], task, name)] = entry
    return out


def _load_pool(pool_path: Path) -> list[str]:
    doc = json.loads(pool_path.read_text(encoding="utf-8"))
    return [s["name"] for s in doc.get("seeds", []) if isinstance(s, dict) and s.get("name")]


_SUFFIX_DERIVED = re.compile(r"(ic|al|ist|ism|ive|ize|ise|ed|ing|ly)$", re.IGNORECASE)
_ACRONYM = re.compile(r"^[A-Z][A-Z0-9-]{1,7}$")


def _is_valid_alias_candidate(candidate: str, seed: str) -> bool:
    c = candidate.strip().lower()
    s = seed.strip().lower()
    if not c or c == s:
        return False
    if c != s and (c in s or s in c):
        if c.rstrip("s") == s.rstrip("s"):
            return True  # plural/singular
        if len(c) <= 6 or len(s) <= 6:
            return True  # acronym vs expansion
        return False
    if not _SUFFIX_DERIVED.search(s) and _SUFFIX_DERIVED.search(c):
        if not c.endswith("s"):
            return False
    return True


def _is_trusted_single_model(candidate: str, seed: str) -> bool:
    """Patterns safe enough to accept from a single model without consensus:
    - Acronym form (ALL CAPS, 2-8 chars) when seed is a longer phrase
    - Expansion from acronym seed to longer proper-noun candidate
    - Clean plural/singular (seed+s or seed-s)
    - Diacritic-removed variant (accented seed → ASCII)
    """
    c = candidate.strip()
    s = seed.strip()
    cl, sl = c.lower(), s.lower()
    if not cl or cl == sl:
        return False
    # Acronym of a multi-word seed
    if _ACRONYM.match(c) and " " in s and len(c) <= 8 and len(s) > 6:
        # Check first letters of seed words roughly match acronym
        seed_initials = "".join(w[0].upper() for w in re.findall(r"[A-Za-z][A-Za-z-]*", s))
        if c.replace("-", "").replace(".", "").upper() == seed_initials[:len(c)]:
            return True
    # Expansion: seed is short all-caps (acronym), candidate is longer proper phrase
    if _ACRONYM.match(s) and len(cl) > len(sl) + 3:
        cand_initials = "".join(w[0].upper() for w in re.findall(r"[A-Za-z][A-Za-z-]*", c))
        if s.upper().replace("-", "").replace(".", "") == cand_initials[:len(s)]:
            return True
    # Clean plural/singular
    if cl.rstrip("s") == sl.rstrip("s") and cl != sl:
        return True
    # Diacritic-only difference
    import unicodedata
    def _strip_diacritics(x: str) -> str:
        return "".join(ch for ch in unicodedata.normalize("NFD", x) if not unicodedata.combining(ch))
    if _strip_diacritics(cl) == _strip_diacritics(sl) and cl != sl:
        return True
    if _strip_diacritics(cl) == sl or cl == _strip_diacritics(sl):
        return True
    return False


def _propose_aliases(per_model: dict[str, dict], seed: str) -> list[str]:
    counter: Counter[str] = Counter()
    display: dict[str, str] = {}
    for model, entry in per_model.items():
        als = entry.get("aliases", [])
        if not isinstance(als, list):
            continue
        for a in als:
            if not isinstance(a, str):
                continue
            k = a.strip().lower()
            if not k:
                continue
            counter[k] += 1
            display.setdefault(k, a.strip())
    proposed: list[str] = []
    for key, count in counter.most_common():
        cand = display[key]
        if count >= 2 and _is_valid_alias_candidate(cand, seed):
            proposed.append(cand)
        elif count == 1 and _is_trusted_single_model(cand, seed):
            # High-confidence pattern (acronym/plural/diacritic) — accept single-model
            proposed.append(cand)
    return proposed


def _propose_is_shell(per_model: dict[str, dict]) -> bool | None:
    votes_true = 0
    votes_false = 0
    for model, entry in per_model.items():
        v = entry.get("is_shell")
        if v is True:
            votes_true += 1
        elif v is False:
            votes_false += 1
    if votes_true >= 4:
        return True
    if votes_false >= 4:
        return False
    return None  # ambiguous — leave uncurated


def _propose_disambig(per_model: dict[str, dict]) -> dict | None:
    path_lists: list[list[str]] = []
    for _, entry in per_model.items():
        paths = entry.get("paths", [])
        if isinstance(paths, list):
            path_lists.append([str(p).strip() for p in paths if isinstance(p, str) and p.strip()])
    n_ambig = sum(1 for p in path_lists if len(p) >= 2)
    n_not = sum(1 for p in path_lists if len(p) < 2)
    total = len(path_lists)
    if total == 0:
        return None
    if n_ambig >= 3:
        # Extract needles — tokens appearing in ≥2 path labels
        token_count: Counter[str] = Counter()
        for plist in path_lists:
            seen_here: set[str] = set()
            joined = " ".join(plist).lower()
            for tok in re.findall(r"[a-zA-Z][a-zA-Z-]{2,}", joined):
                if tok not in seen_here:
                    token_count[tok] += 1
                    seen_here.add(tok)
        # Filter: token must appear in ≥2 models' emissions, and not be the
        # seed name or common stop
        stopish = {"the", "name", "called", "known", "type", "kind"}
        needles = [t for t, c in token_count.most_common(6) if c >= 2 and t not in stopish]
        return {"ambiguous": True, "must_include_any": needles[:5]}
    if n_not >= 4:
        return {"ambiguous": False, "must_include_any": []}
    return None  # split vote — uncurated


def main() -> None:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", default=str(here / "bench_cache.jsonl"))
    parser.add_argument("--pool", default=str(here / "fixtures" / "bench_seeds_100.json"))
    parser.add_argument("--out", default=str(here / "proposals" / "pool_ground_truth.yaml"))
    args = parser.parse_args()

    cache = _load_cache(Path(args.cache))
    names = _load_pool(Path(args.pool))

    proposals: dict[str, dict] = {}
    skipped = {"alias_gen": 0, "shell_classify": 0, "suggest_disambig": 0}
    for name in names:
        entry: dict = {}
        # alias_gen
        per_model_alias = {m: e for (m, t, n), e in cache.items() if t == "alias_gen" and n == name}
        aliases = _propose_aliases(per_model_alias, name)
        if aliases:
            entry["alias_gen"] = {"aliases": aliases, "must_exclude": []}
        else:
            skipped["alias_gen"] += 1

        # shell_classify
        per_model_shell = {m: e for (m, t, n), e in cache.items() if t == "shell_classify" and n == name}
        is_shell = _propose_is_shell(per_model_shell)
        if is_shell is not None:
            entry["shell_classify"] = {"is_shell": is_shell}
        else:
            skipped["shell_classify"] += 1

        # suggest_disambig
        per_model_disambig = {m: e for (m, t, n), e in cache.items() if t == "suggest_disambig" and n == name}
        dis = _propose_disambig(per_model_disambig)
        if dis is not None:
            entry["suggest_disambig"] = dis
        else:
            skipped["suggest_disambig"] += 1

        proposals[name.lower()] = entry

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        "# Auto-proposed pool ground truth — review before promoting into\n"
        "# datasets.POOL_GROUND_TRUTH. Generated by curate_pool.py from\n"
        "# cached bench responses.\n\n" +
        yaml.dump(proposals, sort_keys=True, allow_unicode=True, default_flow_style=False),
        encoding="utf-8",
    )
    print(f"[ok] {out_path}")
    print(f"  items: {len(proposals)}")
    print(f"  skipped: {skipped}")


if __name__ == "__main__":
    main()
