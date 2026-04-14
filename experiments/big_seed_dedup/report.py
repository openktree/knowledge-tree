"""Global-pipeline HTML report.

Layout:
  1. Summary table (registry totals + token/cost)
  2. Global big-seed list (all created big-seeds with path counts + aliases)
  3. Global event timeline — one expandable card per intake showing:
       generated aliases, reverse-alias hits, qdrant near-misses + candidates,
       multiplex LLM I/O, final placement, token cost.
  4. Per-big-seed detail (paths, aliases, facts samples)
"""

from __future__ import annotations

import html as html_module
import json
from datetime import datetime, timezone
from pathlib import Path

from .big_seed import BigSeed, Candidate, Decision, Registry, Usage

_KIND_COLORS = {
    "genesis": "#059669",
    "merge_into_big_seed": "#0ea5e9",
    "merge_into_path": "#7c3aed",
    "split_big_seed": "#f59e0b",
    "new_disambig_path": "#d97706",
    "alias_hit": "#64748b",
}


def _esc(s: str) -> str:
    return html_module.escape(s or "")


def _badge(text: str, color: str, title: str = "") -> str:
    t = f' title="{_esc(title)}"' if title else ""
    style = (
        "display:inline-block;padding:2px 8px;margin:1px;border-radius:10px;"
        f"font-size:0.78em;color:#fff;background:{color};"
    )
    return f'<span{t} style="{style}">{_esc(text)}</span>'


def _pill(text: str, bg: str = "#f1f5f9", fg: str = "#334155", title: str = "") -> str:
    t = f' title="{_esc(title)}"' if title else ""
    style = (
        f"display:inline-block;padding:1px 8px;margin:1px;border-radius:10px;"
        f"font-size:0.78em;color:{fg};background:{bg};border:1px solid #cbd5e1;"
    )
    return f'<span{t} style="{style}">{_esc(text)}</span>'


def _usage_tag(u: Usage | None, label: str) -> str:
    if u is None:
        return ""
    return (
        f"<span class='meta'>{label}: {u.prompt_tokens:,}→{u.completion_tokens:,} tok · "
        f"${u.cost_usd:.5f} · {u.latency_ms}ms</span>"
    )


def _sum_usages(items: list[Usage]) -> Usage:
    out = Usage(kind="total")
    for u in items:
        out.prompt_tokens += u.prompt_tokens
        out.completion_tokens += u.completion_tokens
        out.cost_usd += u.cost_usd
        out.latency_ms += u.latency_ms
    return out


def _candidate_line(c: Candidate) -> str:
    via = _pill(
        c.via,
        bg={"alias": "#dcfce7", "embedding": "#e0f2fe", "both": "#fef9c3"}.get(c.via, "#f1f5f9"),
        fg="#1e293b",
    )
    path_part = f' · path "<b>{_esc(c.path_label)}</b>"' if c.path_label else ""
    extra = ""
    if c.matched_alias:
        extra += f' · alias="<code>{_esc(c.matched_alias)}</code>"'
    if c.matched_source_name:
        extra += f' · source="<code>{_esc(c.matched_source_name)}</code>"'
    return (
        f"<div class='sub'>{via} <b>{c.score:.3f}</b> → "
        f'bigseed "<b>{_esc(c.canonical_name)}</b>"{path_part}{extra}</div>'
    )


def _render_embed_near(scores: list[tuple[str, float]]) -> str:
    if not scores:
        return "<span class='neutral'>(nothing above 0.70)</span>"
    parts: list[str] = []
    for label, score in scores[:10]:
        bg = "#dcfce7" if score >= 0.90 else "#fef9c3" if score >= 0.80 else "#fee2e2"
        parts.append(
            f'<span class="score" style="background:{bg}">'
            f"<b>{score:.3f}</b> {_esc(label)}</span>"
        )
    return "".join(parts)


def _render_event(d: Decision) -> str:
    color = _KIND_COLORS.get(d.kind, "#6b7280")
    badge = _badge(d.kind, color, d.reason)

    target = _esc(d.target_big_seed_canonical or "-")
    path_tail = f' · path "<b>{_esc(d.target_path_label)}</b>"' if d.target_path_label else ""

    facts_html = "".join(f"<li>{_esc(s)}</li>" for s in d.incoming_fact_samples) or "<li class='neutral'>(none)</li>"

    alias_block = ""
    if d.alias_gen_response is not None or d.incoming_aliases:
        aliases_html = " ".join(_pill(a, "#dbeafe", "#1e40af") for a in d.incoming_aliases)
        alias_block = (
            "<div class='sub'><b>alias_gen →</b> "
            f"{aliases_html or '<span class=neutral>(none)</span>'} "
            f"{_usage_tag(d.alias_gen_usage, 'alias_gen')}</div>"
        )

    rev_html = ""
    if d.reverse_alias_hits:
        rev_html = "<div class='sub'><b>reverse alias hits:</b></div>" + "".join(
            _candidate_line(c) for c in d.reverse_alias_hits
        )
    else:
        rev_html = "<div class='sub'><b>reverse alias hits:</b> <span class='neutral'>(none)</span></div>"

    emb_cands_html = ""
    if d.embed_candidates:
        emb_cands_html = "<div class='sub'><b>embedding candidates (≥0.90):</b></div>" + "".join(
            _candidate_line(c) for c in d.embed_candidates
        )
    else:
        emb_cands_html = "<div class='sub'><b>embedding candidates (≥0.90):</b> <span class='neutral'>(none)</span></div>"

    near_html = f"<div class='sub'><b>qdrant near-matches (≥0.70):</b> {_render_embed_near(d.all_embed_scores)}</div>"

    mp_html = ""
    if d.multiplex_response:
        mp_html = (
            "<div class='sub'><b>multiplex LLM →</b> "
            f"<code>{_esc(json.dumps(d.multiplex_response, ensure_ascii=False))}</code> "
            f"{_usage_tag(d.multiplex_usage, 'multiplex')}</div>"
        )

    split_html = ""
    if d.split_paths:
        rows = "".join(
            f"<li><b>{_esc(p['label'])}</b> <span class='neutral'>aliases: {p.get('aliases', []) or []}</span></li>"
            for p in d.split_paths
        )
        split_html = f"<div class='sub'><b>split paths:</b><ul>{rows}</ul></div>"

    return f"""
    <details class="event">
      <summary>
        <span class="step">#{d.step}</span>
        <b>{_esc(d.incoming_name)}</b>
        <span class="neutral">({d.incoming_fact_count} facts)</span>
        → {badge} → bigseed "<b>{target}</b>"{path_tail}
      </summary>
      <div class="body">
        <div class="sub"><b>reason:</b> {_esc(d.reason)}</div>
        <div class="sub"><b>incoming fact samples:</b><ul>{facts_html}</ul></div>
        {alias_block}
        {rev_html}
        {emb_cands_html}
        {near_html}
        {mp_html}
        {split_html}
      </div>
    </details>
    """


def _render_big_seed(big: BigSeed) -> str:
    if big.paths:
        body = "".join(
            f"""<div class="path">
              <div class="path-head">
                <span class="path-id">{_esc(p.id)}</span>
                <b class="path-label">{_esc(p.label)}</b>
              </div>
              <div class="sub"><b>aliases</b> ({len(p.aliases)}): {' '.join(_pill(a, '#fef3c7', '#92400e') for a in p.aliases) or '<span class=neutral>none</span>'}</div>
              <div class="sub"><b>embeddings</b> ({len(p.embeddings)}): {' '.join(_pill(nv.source_name, '#e0e7ff', '#3730a3') for nv in p.embeddings)}</div>
              <details><summary>{len(p.facts)} fact sample(s)</summary>
                <ul>{''.join(f'<li>{_esc(f.content[:260])}</li>' for f in p.facts[:5])}</ul>
              </details>
            </div>"""
            for p in big.paths
        )
        summary = f"<b>ambiguous</b> · {len(big.paths)} paths · {sum(len(p.aliases) for p in big.paths)} aliases"
    else:
        aliases = " ".join(_pill(a, "#fef3c7", "#92400e") for a in big.aliases)
        embeds = " ".join(_pill(nv.source_name, "#e0e7ff", "#3730a3") for nv in big.embeddings)
        body = f"""
          <div class="sub"><b>aliases</b> ({len(big.aliases)}): {aliases or '<span class=neutral>none</span>'}</div>
          <div class="sub"><b>embeddings</b> ({len(big.embeddings)}): {embeds}</div>
          <details><summary>{len(big.facts)} fact sample(s)</summary>
            <ul>{''.join(f'<li>{_esc(f.content[:260])}</li>' for f in big.facts[:5])}</ul>
          </details>
        """
        summary = f"<b>flat</b> · {len(big.aliases)} aliases · {len(big.embeddings)} embeddings"

    return f"""
    <div class="bigseed">
      <div class="canonical">
        <span class="path-id">{_esc(big.id)}</span>
        canonical: <b>{_esc(big.canonical_name)}</b>
        <span class="neutral">({_esc(big.node_type)})</span>
        <span class="neutral">— {summary}</span>
      </div>
      {body}
    </div>
    """


_CSS = """
  * { box-sizing:border-box; margin:0; padding:0; }
  body { font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
         max-width:1500px; margin:0 auto; padding:20px; background:#f8fafc; color:#1e293b; }
  h1 { font-size:1.6em; margin-bottom:4px; }
  h2 { font-size:1.3em; margin:28px 0 10px; border-bottom:2px solid #e2e8f0; padding-bottom:6px; }
  h3 { font-size:1.05em; margin:16px 0 8px; color:#334155; }
  .meta { color:#64748b; font-size:0.78em; margin-left:6px; }
  .neutral { color:#94a3b8; }
  .mono { font-family:ui-monospace,Consolas,monospace; font-size:0.82em; }
  .reason { font-size:0.82em; color:#475569; max-width:340px; }
  table { border-collapse:collapse; width:100%; margin:8px 0; font-size:0.88em; }
  th,td { border:1px solid #e2e8f0; padding:6px 10px; text-align:left; vertical-align:top; }
  th { background:#f1f5f9; font-weight:600; }
  tr:hover { background:#f8fafc; }
  .num { text-align:right; font-variant-numeric:tabular-nums; }
  .section { background:#fff; border-radius:8px; padding:18px; margin:14px 0;
             box-shadow:0 1px 3px rgba(0,0,0,0.08); }
  details.event { border:1px solid #e2e8f0; border-radius:6px; margin:6px 0; background:#fff; }
  details.event > summary { padding:8px 12px; cursor:pointer; list-style:none;
                            display:flex; align-items:center; gap:8px; flex-wrap:wrap; font-size:0.9em; }
  details.event > summary::-webkit-details-marker { display:none; }
  details.event > .body { padding:8px 14px 12px; background:#f8fafc; border-top:1px solid #e2e8f0; }
  details.event[open] > summary { background:#f1f5f9; border-bottom:1px solid #e2e8f0; }
  .step { font-family:ui-monospace,Consolas,monospace; color:#64748b; font-size:0.82em; }
  .sub { margin:4px 0; font-size:0.88em; color:#334155; }
  .sub ul { margin-left:20px; }
  .score { display:inline-block; padding:2px 8px; margin:1px;
           border-radius:10px; font-size:0.78em; border:1px solid #cbd5e1; }
  .bigseed { background:#fffbeb; border:1px solid #fcd34d; border-radius:8px; padding:14px; margin:8px 0; }
  .canonical { font-size:1.02em; margin-bottom:8px; }
  .path { border-left:3px solid #f59e0b; background:#fff; padding:8px 12px; margin:6px 0; border-radius:4px; }
  .path-head { display:flex; align-items:baseline; gap:8px; flex-wrap:wrap; }
  .path-id { font-family:ui-monospace,Consolas,monospace; font-size:0.78em; color:#64748b; }
  .path-label { color:#1e293b; }
  code { font-family:ui-monospace,Consolas,monospace; font-size:0.82em;
         background:#f1f5f9; padding:1px 4px; border-radius:3px; word-break:break-all; }
"""


def _render_shell_section(shell_seeds: list) -> str:
    """Audit seeds short-circuited by the alias_gen shell classifier."""
    parts: list[str] = []
    parts.append("<div class='section'><h2>Shell seeds (alias_gen short-circuit)</h2>")
    parts.append(
        f"<div class='meta'>{len(shell_seeds)} candidate(s) classified as shell nouns — "
        "never embedded, never merged, never promoted. "
        "Epistemological filter via LLM at alias_gen time.</div>"
    )
    parts.append("<table>")
    parts.append(
        "<tr><th>name</th><th>node_type</th><th class='num'>facts</th>"
        "<th>shell reason</th><th>aliases</th></tr>"
    )
    shell_seeds_sorted = sorted(shell_seeds, key=lambda s: (-getattr(s, 'fact_count', 0), getattr(s, 'name', '').lower()))
    for s in shell_seeds_sorted:
        aliases = " ".join(_pill(a, "#dbeafe", "#1e40af") for a in getattr(s, 'aliases', []) or [])
        parts.append(
            "<tr>"
            f"<td><b>{_esc(getattr(s, 'name', ''))}</b></td>"
            f"<td>{_esc(getattr(s, 'node_type', ''))}</td>"
            f"<td class='num'>{getattr(s, 'fact_count', 0)}</td>"
            f"<td class='reason'>{_esc(getattr(s, 'reason', ''))}</td>"
            f"<td>{aliases or '<span class=neutral>-</span>'}</td>"
            "</tr>"
        )
    parts.append("</table></div>")
    return "".join(parts)


def _render_ignored_section(section: dict) -> str:
    """Render audit of spaCy candidates dropped by the generic filter."""
    filter_on = section.get("filter_on", True)
    fact_count = section.get("fact_count", 0)
    stats = section.get("stats", {}) or {}
    ignored = section.get("ignored", []) or []

    kept = stats.get("unique_kept", 0)
    total_seen = kept + stats.get("unique_ignored", 0)

    parts: list[str] = []
    parts.append("<div class='section'><h2>Ignored seeds (pre-filter audit)</h2>")
    parts.append(
        f"<div class='meta'>filter_on={filter_on} · raw_facts={fact_count} · "
        f"unique candidates seen: {total_seen} · kept: {kept} · "
        f"ignored: {stats.get('unique_ignored', 0)}</div>"
    )

    if not filter_on:
        parts.append("<p class='neutral'>Generic filter disabled for this run — no rejections recorded.</p>")
        parts.append("</div>")
        return "".join(parts)

    # Stats breakdown — only non-zero reasons
    parts.append("<table>")
    parts.append("<tr><th>Reason</th><th class='num'>Rejected unique</th><th class='num'>Rejected mentions</th></tr>")
    for reason in ("ner_label", "regex", "concreteness"):
        unique_count = sum(1 for i in ignored if i["reason"] == reason)
        mention_count = stats.get(f"ignored_{reason}", 0)
        if unique_count == 0 and mention_count == 0:
            continue
        parts.append(
            f"<tr><td>{_esc(reason)}</td>"
            f"<td class='num'>{unique_count}</td>"
            f"<td class='num'>{mention_count}</td></tr>"
        )
    parts.append("</table>")

    # Per-filter collapsible tables
    for reason, header_extra in (
        ("ner_label", "Dropped because spaCy NER labeled it as a numeric/temporal span."),
        ("regex", "Dropped because the surface string is a pure number / date / page marker."),
        ("concreteness", f"Dropped because single-token head-noun concreteness < {CONCRETENESS_THRESHOLD}."),
    ):
        rows = [i for i in ignored if i["reason"] == reason]
        if not rows:
            continue
        parts.append(
            f"<details><summary><b>{_esc(reason)}</b> — {len(rows)} unique · {header_extra}</summary>"
        )
        parts.append("<table>")
        parts.append(
            "<tr><th>name</th><th>head_lemma</th><th>tok</th><th>source</th>"
            "<th>ner_label</th><th>detail</th><th class='num'>facts</th></tr>"
        )
        rows.sort(key=lambda r: (-r.get("fact_count", 0), r.get("name", "").lower()))
        for r in rows:
            parts.append(
                "<tr>"
                f"<td><b>{_esc(r.get('name', ''))}</b></td>"
                f"<td class='mono'>{_esc(r.get('head_lemma') or '')}</td>"
                f"<td class='num'>{r.get('token_count', 0)}</td>"
                f"<td>{_esc(r.get('source', ''))}</td>"
                f"<td class='mono'>{_esc(r.get('ner_label') or '')}</td>"
                f"<td class='mono'>{_esc(r.get('detail', ''))}</td>"
                f"<td class='num'>{r.get('fact_count', 0)}</td>"
                "</tr>"
            )
        parts.append("</table></details>")

    parts.append("</div>")
    return "".join(parts)


# Borderline threshold (kept in sync with generic_filter.CONCRETENESS_THRESHOLD
# but we avoid importing to keep this module usable outside the facts experiment)
CONCRETENESS_THRESHOLD = 2.5


def generate_report(
    registry: Registry,
    output_path: Path,
    model_name: str,
    *,
    ignored_section: dict | None = None,
) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    alias_usages = [d.alias_gen_usage for d in registry.history if d.alias_gen_usage]
    multi_usages = [d.multiplex_usage for d in registry.history if d.multiplex_usage]
    at = _sum_usages(alias_usages)
    mt = _sum_usages(multi_usages)
    total_prompt = at.prompt_tokens + mt.prompt_tokens
    total_compl = at.completion_tokens + mt.completion_tokens
    total_cost = at.cost_usd + mt.cost_usd

    kind_counts: dict[str, int] = {}
    for d in registry.history:
        kind_counts[d.kind] = kind_counts.get(d.kind, 0) + 1

    parts: list[str] = []
    parts.append(f"<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
                 f"<title>Big-Seed Dedup Experiment v2</title><style>{_CSS}</style></head><body>")
    parts.append("<h1>Big-Seed Dedup Experiment — v2 (global intake)</h1>")
    parts.append(
        f"<div class='meta'>Generated {now} · model: {_esc(model_name)} · "
        f"{len(registry.big_seeds)} big-seeds · {len(registry.history)} decisions</div>"
    )

    # Summary
    parts.append("<div class='section'><h2>Summary</h2><table>")
    parts.append(
        "<tr><th>Metric</th><th class='num'>Value</th></tr>"
        f"<tr><td>big-seeds created</td><td class='num'>{len(registry.big_seeds)}</td></tr>"
        f"<tr><td>decisions</td><td class='num'>{len(registry.history)}</td></tr>"
        f"<tr><td>alias_gen calls</td><td class='num'>{len(alias_usages)}</td></tr>"
        f"<tr><td>multiplex calls</td><td class='num'>{len(multi_usages)}</td></tr>"
        f"<tr><td>prompt tokens</td><td class='num'>{total_prompt:,}</td></tr>"
        f"<tr><td>completion tokens</td><td class='num'>{total_compl:,}</td></tr>"
        f"<tr><td>total cost</td><td class='num'>${total_cost:.5f}</td></tr>"
    )
    for k, v in sorted(kind_counts.items(), key=lambda x: -x[1]):
        parts.append(f"<tr><td>kind: {_esc(k)}</td><td class='num'>{v}</td></tr>")
    parts.append("</table>")
    parts.append(
        f"<div class='meta'>alias_gen: {at.prompt_tokens:,}→{at.completion_tokens:,} tok · "
        f"${at.cost_usd:.5f} · multiplex: {mt.prompt_tokens:,}→{mt.completion_tokens:,} tok · "
        f"${mt.cost_usd:.5f}</div></div>"
    )

    # Ignored seeds audit (only when ignored_section is provided)
    if ignored_section is not None:
        parts.append(_render_ignored_section(ignored_section))

    # Shell seeds audit — short-circuited by alias_gen LLM classifier
    if registry.shell_seeds:
        parts.append(_render_shell_section(registry.shell_seeds))

    # Global big-seed list
    parts.append("<div class='section'><h2>Global big-seed list</h2>")
    for b in registry.big_seeds:
        parts.append(_render_big_seed(b))
    parts.append("</div>")

    # Global event timeline
    parts.append("<div class='section'><h2>Event timeline (global)</h2>")
    for d in registry.history:
        parts.append(_render_event(d))
    parts.append("</div>")

    parts.append("</body></html>")
    output_path.write_text("\n".join(parts), encoding="utf-8")
    print(f"HTML report written: {output_path}")
