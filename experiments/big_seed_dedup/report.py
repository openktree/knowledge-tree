"""HTML report generator for big-seed dedup replay results."""

from __future__ import annotations

import html as html_module
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


def _usage_cell(u: Usage | None) -> str:
    if u is None:
        return "<span class='neutral'>-</span>"
    return (
        f"<span class='num'>{u.prompt_tokens:,}→{u.completion_tokens:,}</span>"
        f"<div class='meta'>{_esc(u.model)} · ${u.cost_usd:.5f} · {u.latency_ms}ms</div>"
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
    """Return (alias_gen_usages, multiplex_usages)."""
    alias_u: list[Usage] = []
    multi_u: list[Usage] = []
    for p in big.paths:
        if p.alias_gen_usage:
            alias_u.append(p.alias_gen_usage)
    for d in big.history:
        if d.multiplex_usage:
            multi_u.append(d.multiplex_usage)
    return alias_u, multi_u


def _decision_badge(d: Decision) -> str:
    color = _KIND_COLORS.get(d.kind, "#6b7280")
    return _badge(d.kind, color, d.reason)


def _render_path(p, big: BigSeed) -> str:
    alias_html = " ".join(_badge(a, "#0ea5e9", "LLM-generated alias") for a in p.aliases)
    observed_html = " ".join(_badge(o, "#64748b", "observed surface form") for o in p.observed_names)
    facts_html = "".join(f"<li>{_esc(f.content[:260])}</li>" for f in p.facts[:5])
    alias_cost = ""
    if p.alias_gen_usage:
        alias_cost = (
            f"<div class='meta'>alias_gen: {p.alias_gen_usage.prompt_tokens:,}→"
            f"{p.alias_gen_usage.completion_tokens:,} tok · "
            f"${p.alias_gen_usage.cost_usd:.5f}</div>"
        )
    return f"""
    <div class='path'>
      <div><b>path_id={_esc(p.id)}</b> · label=<b>{_esc(p.label)}</b></div>
      <div class='meta'>aliases ({len(p.aliases)}): {alias_html or '<span class=neutral>none</span>'}</div>
      <div class='meta'>observed ({len(p.observed_names)}): {observed_html or '-'}</div>
      {alias_cost}
      <details><summary>{len(p.facts)} fact(s) stored</summary><ul>{facts_html}</ul></details>
    </div>
    """


def _render_timeline(big: BigSeed) -> str:
    rows: list[str] = []
    rows.append(
        "<tr><th>#</th><th>Incoming</th><th>Decision</th><th>Routed to</th>"
        "<th>Best embed</th><th>Multiplex tokens</th><th>alias_gen tokens</th>"
        "<th>Reason</th></tr>"
    )
    for d in big.history:
        routed = d.routed_to_path_label or "-"
        embed = f"{d.best_embed_score:.3f}" if d.best_embed_score else "-"
        rows.append(
            "<tr>"
            f"<td class='num'>{d.step}</td>"
            f"<td>{_esc(d.incoming_name)}<div class='meta'>{d.incoming_fact_count} facts</div></td>"
            f"<td>{_decision_badge(d)}</td>"
            f"<td>{_esc(routed)}</td>"
            f"<td class='num'>{embed}</td>"
            f"<td>{_usage_cell(d.multiplex_usage)}</td>"
            f"<td>{_usage_cell(d.alias_gen_usage)}</td>"
            f"<td class='reason'>{_esc(d.reason)}</td>"
            "</tr>"
        )
    return "<table>" + "\n".join(rows) + "</table>"


def _render_prod_diff(rr: ReplayResult) -> str:
    if not rr.prod_merges:
        return "<p class='neutral'>(no prod merges recorded for this fixture)</p>"
    rows = ["<tr><th>Op</th><th>Source</th><th>Target</th><th>Reason</th><th>Facts moved</th><th>When</th></tr>"]
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


def generate_report(results: list[ReplayResult], output_path: Path, model_name: str) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # ── Grand totals ─────────────────────────────────────────────────
    all_alias: list[Usage] = []
    all_multi: list[Usage] = []
    for rr in results:
        a, m = _collect_usages(rr.big_seed)
        all_alias.extend(a)
        all_multi.extend(m)
    alias_total = _sum_usages(all_alias)
    multi_total = _sum_usages(all_multi)

    parts: list[str] = []
    parts.append(f"""<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>
<title>Big-Seed Dedup Experiment</title>
<style>
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
         max-width:1500px; margin:0 auto; padding:20px; background:#f8fafc; color:#1e293b; }}
  h1 {{ font-size:1.6em; margin-bottom:4px; }}
  h2 {{ font-size:1.3em; margin:30px 0 12px; border-bottom:2px solid #e2e8f0; padding-bottom:6px; }}
  h3 {{ font-size:1.05em; margin:18px 0 8px; }}
  .meta {{ color:#64748b; font-size:0.8em; }}
  table {{ border-collapse:collapse; width:100%; margin:8px 0; font-size:0.88em; }}
  th,td {{ border:1px solid #e2e8f0; padding:6px 10px; text-align:left; vertical-align:top; }}
  th {{ background:#f1f5f9; font-weight:600; }}
  tr:hover {{ background:#f8fafc; }}
  .num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  .section {{ background:#fff; border-radius:8px; padding:18px; margin:14px 0; box-shadow:0 1px 3px rgba(0,0,0,0.08); }}
  .neutral {{ color:#94a3b8; }}
  .mono {{ font-family:ui-monospace,Consolas,monospace; font-size:0.82em; }}
  .reason {{ font-size:0.82em; color:#475569; max-width:320px; }}
  .path {{ border-left:3px solid #f59e0b; padding:6px 10px; margin:6px 0; background:#fffbeb; border-radius:4px; }}
  details summary {{ cursor:pointer; color:#2563eb; font-size:0.85em; }}
  ul {{ margin-left:18px; }}
</style></head><body>""")
    parts.append(f"<h1>Big-Seed Dedup Experiment</h1>")
    parts.append(f"<div class='meta'>Generated {now} · model: {_esc(model_name)} · {len(results)} fixture(s)</div>")

    # ── Summary ─────────────────────────────────────────────────────
    parts.append("<div class='section'><h2>Summary</h2><table>")
    parts.append(
        "<tr><th>Fixture</th><th>Target</th><th class='num'>Family</th>"
        "<th class='num'>Processed</th><th class='num'>Paths</th>"
        "<th class='num'>alias_gen calls</th><th class='num'>multiplex calls</th>"
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
    # grand total
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
        "<div class='meta'>"
        f"alias_gen subtotal: {alias_total.prompt_tokens:,}→{alias_total.completion_tokens:,} tok · ${alias_total.cost_usd:.5f} · "
        f"multiplex subtotal: {multi_total.prompt_tokens:,}→{multi_total.completion_tokens:,} tok · ${multi_total.cost_usd:.5f}"
        "</div></div>"
    )

    # ── Per-fixture detail ─────────────────────────────────────────
    for rr in results:
        big = rr.big_seed
        parts.append("<div class='section'>")
        parts.append(f"<h2>{_esc(rr.fixture_label)} — {_esc(rr.target_name)}</h2>")
        parts.append(
            f"<div class='meta'>canonical = <b>{_esc(big.canonical_name)}</b> "
            f"({_esc(big.node_type)}) · parent aliases: {big.aliases or 'none'}</div>"
        )

        parts.append("<h3>Final big-seed structure</h3>")
        for p in big.paths:
            parts.append(_render_path(p, big))

        parts.append("<h3>Replay timeline</h3>")
        parts.append(_render_timeline(big))

        parts.append("<h3>Prod merge audit (ground truth)</h3>")
        parts.append(_render_prod_diff(rr))

        parts.append("</div>")

    parts.append("</body></html>")

    output_path.write_text("\n".join(parts), encoding="utf-8")
    print(f"HTML report written: {output_path}")
