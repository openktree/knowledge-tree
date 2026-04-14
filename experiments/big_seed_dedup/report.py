"""HTML report generator for big-seed dedup replay results.

Two views per fixture:
1. **Event history** — one collapsible card per admit step showing incoming
   name, fact samples, embedding distances to every existing path, full LLM
   I/O (alias_gen + multiplex), final decision, token cost.
2. **Final big-seed structure** — canonical + parent merged forms + each
   path (label, known_aliases from LLM alias_gen, merged_surface_forms from
   multiplex routing, fact samples).

Aliases are split deliberately:
- known_aliases: LLM-generated at path birth. Real-world alternative names
  for the SAME concept (e.g. "JFK" for "John F. Kennedy").
- merged_surface_forms: surface forms the multiplexer routed into the path.
  These are "embedding-ambiguous" — LLM/human may still see them as slightly
  different, but the system chose to collapse them into one path.
"""

from __future__ import annotations

import html as html_module
import json
from datetime import datetime, timezone
from pathlib import Path

from .big_seed import BigSeed, Decision, Usage
from .replay import ReplayResult


_KIND_COLORS = {
    "seed_init": "#64748b",
    "alias_match": "#059669",
    "embed_auto_route": "#0ea5e9",
    "embed_reject": "#dc2626",
    "llm_merge_path": "#7c3aed",
    "llm_alias_to_parent": "#14b8a6",
    "llm_new_path": "#f59e0b",
    "llm_reject": "#ef4444",
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


def _usage_tag(u: Usage | None, kind_label: str) -> str:
    if u is None:
        return ""
    return (
        f"<span class='meta'>{kind_label}: "
        f"{u.prompt_tokens:,}→{u.completion_tokens:,} tok · "
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


def _collect_usages(big: BigSeed) -> tuple[list[Usage], list[Usage]]:
    alias_u = [p.alias_gen_usage for p in big.paths if p.alias_gen_usage]
    multi_u = [d.multiplex_usage for d in big.history if d.multiplex_usage]
    return alias_u, multi_u


def _render_embed_scores(scores: dict[str, float]) -> str:
    if not scores:
        return "<span class='neutral'>(no existing paths yet)</span>"
    parts: list[str] = []
    for label, score in sorted(scores.items(), key=lambda x: -x[1]):
        bg = "#dcfce7" if score >= 0.95 else "#fef9c3" if score >= 0.70 else "#fee2e2"
        parts.append(
            f'<span class="score" style="background:{bg}">'
            f'<b>{score:.3f}</b> <span class="neutral">vs</span> {_esc(label)}'
            f'</span>'
        )
    return "".join(parts)


def _render_event(d: Decision, step_label: str = "") -> str:
    color = _KIND_COLORS.get(d.kind, "#6b7280")
    badge = _badge(d.kind, color, d.reason)
    routed = d.routed_to_path_label or "-"

    facts_html = "".join(f"<li>{_esc(s)}</li>" for s in d.incoming_fact_samples)
    if not facts_html:
        facts_html = "<li class='neutral'>(none)</li>"

    mp_html = ""
    if d.multiplex_response:
        mp_html = (
            "<div class='sub'><b>multiplex LLM →</b> "
            f"<code>{_esc(json.dumps(d.multiplex_response, ensure_ascii=False))}</code> "
            f"{_usage_tag(d.multiplex_usage, 'multiplex')}</div>"
        )

    ag_html = ""
    if d.alias_gen_response:
        ag_html = (
            "<div class='sub'><b>alias_gen LLM →</b> "
            f"<code>{_esc(json.dumps(d.alias_gen_response, ensure_ascii=False))}</code> "
            f"{_usage_tag(d.alias_gen_usage, 'alias_gen')}</div>"
        )

    embed_html = (
        f"<div class='sub'><b>embed distances</b> "
        f"(best={d.best_embed_score:.3f}): {_render_embed_scores(d.embed_scores)}</div>"
    ) if d.embed_scores or d.kind not in ("seed_init", "alias_match") else ""

    alias_gate = f" · gate=<code>{_esc(d.alias_gate)}</code>" if d.alias_gate else ""

    return f"""
    <details class="event" open>
      <summary>
        <span class="step">#{d.step}{_esc(step_label)}</span>
        <b>{_esc(d.incoming_name)}</b>
        <span class="neutral">({d.incoming_fact_count} facts)</span>
        → {badge}
        → <b>{_esc(routed)}</b>{alias_gate}
      </summary>
      <div class="body">
        <div class="sub"><b>reason:</b> {_esc(d.reason)}</div>
        <div class="sub"><b>incoming fact samples:</b><ul>{facts_html}</ul></div>
        {embed_html}
        {mp_html}
        {ag_html}
      </div>
    </details>
    """


def _render_path(p) -> str:
    known = " ".join(_pill(a, "#dbeafe", "#1e40af", "LLM-generated known alias") for a in p.known_aliases)
    merged = " ".join(_pill(a, "#fef3c7", "#92400e", "absorbed surface form") for a in p.merged_surface_forms)
    facts = "".join(f"<li>{_esc(f.content[:260])}</li>" for f in p.facts[:5])
    alias_cost = _usage_tag(p.alias_gen_usage, "alias_gen")
    return f"""
    <div class="path">
      <div class="path-head">
        <span class="path-id">{_esc(p.id)}</span>
        <b class="path-label">{_esc(p.label)}</b>
        {alias_cost}
      </div>
      <div class="sub"><b>known aliases</b> ({len(p.known_aliases)}): {known or '<span class="neutral">none</span>'}</div>
      <div class="sub"><b>merged surface forms</b> ({len(p.merged_surface_forms)}): {merged or '<span class="neutral">none</span>'}</div>
      <details><summary>{len(p.facts)} fact sample(s)</summary><ul>{facts}</ul></details>
    </div>
    """


def _render_big_seed(big: BigSeed) -> str:
    merged_parent = " ".join(
        _pill(a, "#fef3c7", "#92400e", "absorbed at parent level") for a in big.merged_surface_forms
    )
    paths = "".join(_render_path(p) for p in big.paths)
    return f"""
    <div class="bigseed">
      <div class="canonical">canonical: <b>{_esc(big.canonical_name)}</b>
        <span class="neutral">({_esc(big.node_type)})</span></div>
      <div class="sub"><b>parent merged surface forms</b> ({len(big.merged_surface_forms)}):
        {merged_parent or '<span class="neutral">none</span>'}</div>
      <div class="paths">
        <h4>paths ({len(big.paths)})</h4>
        {paths}
      </div>
    </div>
    """


def _render_prod_audit(rr: ReplayResult) -> str:
    if not rr.prod_merges:
        return "<p class='neutral'>(no prod merges recorded)</p>"
    rows = ["<tr><th>op</th><th>source</th><th>target</th><th>reason</th><th class='num'>facts</th><th>when</th></tr>"]
    for m in rr.prod_merges:
        rows.append(
            "<tr>"
            f"<td>{_esc(m.get('operation',''))}</td>"
            f"<td class='mono'>{_esc(m.get('source',''))}</td>"
            f"<td class='mono'>{_esc(m.get('target',''))}</td>"
            f"<td class='reason'>{_esc(m.get('reason') or '')}</td>"
            f"<td class='num'>{len(m.get('fact_ids_moved') or [])}</td>"
            f"<td class='meta'>{_esc(m.get('created_at') or '')}</td>"
            "</tr>"
        )
    return "<table>" + "\n".join(rows) + "</table>"


_CSS = """
  * { box-sizing:border-box; margin:0; padding:0; }
  body { font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
         max-width:1500px; margin:0 auto; padding:20px; background:#f8fafc; color:#1e293b; }
  h1 { font-size:1.6em; margin-bottom:4px; }
  h2 { font-size:1.3em; margin:28px 0 10px; border-bottom:2px solid #e2e8f0; padding-bottom:6px; }
  h3 { font-size:1.05em; margin:16px 0 8px; color:#334155; }
  h4 { font-size:0.95em; margin:10px 0 6px; color:#475569; }
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
  .canonical { font-size:1.05em; margin-bottom:8px; }
  .path { border-left:3px solid #f59e0b; background:#fff; padding:8px 12px; margin:6px 0; border-radius:4px; }
  .path-head { display:flex; align-items:baseline; gap:8px; flex-wrap:wrap; }
  .path-id { font-family:ui-monospace,Consolas,monospace; font-size:0.78em; color:#64748b; }
  .path-label { color:#1e293b; }
  code { font-family:ui-monospace,Consolas,monospace; font-size:0.82em;
         background:#f1f5f9; padding:1px 4px; border-radius:3px; word-break:break-all; }
"""


def generate_report(results: list[ReplayResult], output_path: Path, model_name: str) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    all_alias: list[Usage] = []
    all_multi: list[Usage] = []
    for rr in results:
        a, m = _collect_usages(rr.big_seed)
        all_alias.extend(a)
        all_multi.extend(m)
    alias_total = _sum_usages(all_alias)
    multi_total = _sum_usages(all_multi)

    parts: list[str] = []
    parts.append(f"<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
                 f"<title>Big-Seed Dedup Experiment</title><style>{_CSS}</style></head><body>")
    parts.append("<h1>Big-Seed Dedup Experiment</h1>")
    parts.append(f"<div class='meta'>Generated {now} · model: {_esc(model_name)} · {len(results)} fixture(s)</div>")

    # ── Summary ─────────────────────────────────────────────────────
    parts.append("<div class='section'><h2>Summary</h2><table>")
    parts.append(
        "<tr><th>Fixture</th><th>Target</th><th class='num'>Family</th>"
        "<th class='num'>Processed</th><th class='num'>Paths</th>"
        "<th class='num'>alias_gen</th><th class='num'>multiplex</th>"
        "<th class='num'>Prompt tok</th><th class='num'>Compl tok</th>"
        "<th class='num'>Cost</th><th class='num'>Prod merges</th></tr>"
    )
    for rr in results:
        a, m = _collect_usages(rr.big_seed)
        at = _sum_usages(a)
        mt = _sum_usages(m)
        total_prompt = at.prompt_tokens + mt.prompt_tokens
        total_compl = at.completion_tokens + mt.completion_tokens
        total_cost = at.cost_usd + mt.cost_usd
        parts.append(
            "<tr>"
            f"<td>{_esc(rr.fixture_label)}</td>"
            f"<td><b>{_esc(rr.target_name)}</b></td>"
            f"<td class='num'>{rr.members_processed + rr.members_skipped + 1}</td>"
            f"<td class='num'>{rr.members_processed}</td>"
            f"<td class='num'>{len(rr.big_seed.paths)}</td>"
            f"<td class='num'>{len(a)}</td>"
            f"<td class='num'>{len(m)}</td>"
            f"<td class='num'>{total_prompt:,}</td>"
            f"<td class='num'>{total_compl:,}</td>"
            f"<td class='num'>${total_cost:.5f}</td>"
            f"<td class='num'>{len(rr.prod_merges)}</td>"
            "</tr>"
        )
    grand_prompt = alias_total.prompt_tokens + multi_total.prompt_tokens
    grand_compl = alias_total.completion_tokens + multi_total.completion_tokens
    grand_cost = alias_total.cost_usd + multi_total.cost_usd
    parts.append(
        "<tr style='background:#fef3c7;font-weight:600'>"
        "<td colspan=5>GRAND TOTAL</td>"
        f"<td class='num'>{len(all_alias)}</td>"
        f"<td class='num'>{len(all_multi)}</td>"
        f"<td class='num'>{grand_prompt:,}</td>"
        f"<td class='num'>{grand_compl:,}</td>"
        f"<td class='num'>${grand_cost:.5f}</td>"
        "<td></td></tr>"
    )
    parts.append("</table>")
    parts.append(
        f"<div class='meta'>alias_gen: {alias_total.prompt_tokens:,}→{alias_total.completion_tokens:,} tok · "
        f"${alias_total.cost_usd:.5f} · multiplex: {multi_total.prompt_tokens:,}→"
        f"{multi_total.completion_tokens:,} tok · ${multi_total.cost_usd:.5f}</div></div>"
    )

    # ── Per-fixture ─────────────────────────────────────────────────
    for rr in results:
        big = rr.big_seed
        parts.append("<div class='section'>")
        parts.append(f"<h2>{_esc(rr.fixture_label)} — {_esc(rr.target_name)}</h2>")

        parts.append("<h3>Final big-seed structure</h3>")
        parts.append(_render_big_seed(big))

        parts.append("<h3>Event history (per admitted seed)</h3>")
        for d in big.history:
            parts.append(_render_event(d))

        parts.append("<h3>Prod merge audit (ground truth)</h3>")
        parts.append(_render_prod_audit(rr))

        parts.append("</div>")

    parts.append("</body></html>")
    output_path.write_text("\n".join(parts), encoding="utf-8")
    print(f"HTML report written: {output_path}")
