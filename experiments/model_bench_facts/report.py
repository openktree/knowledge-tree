"""HTML report for fact-extraction bench."""

from __future__ import annotations

import html as html_module
from datetime import datetime, timezone
from pathlib import Path


def _esc(s: str) -> str:
    return html_module.escape(str(s) if s is not None else "")


def _bar(pct: float, color: str, width: int = 140) -> str:
    pct = max(0.0, min(100.0, pct))
    return (
        f'<div style="background:#f1f5f9;width:{width}px;border-radius:3px;'
        f'display:inline-block;vertical-align:middle">'
        f'<div style="width:{pct:.1f}%;background:{color};height:10px;border-radius:3px"></div></div>'
    )


_CSS = """
  * { box-sizing:border-box; margin:0; padding:0; }
  body { font-family:-apple-system,BlinkMacSystemFont,Roboto,sans-serif;
         max-width:1600px; margin:0 auto; padding:20px; background:#f8fafc; color:#1e293b; }
  h1 { font-size:1.5em; margin-bottom:4px; }
  h2 { font-size:1.2em; margin:22px 0 8px; border-bottom:1px solid #e2e8f0; padding-bottom:4px; }
  .meta { color:#64748b; font-size:0.82em; margin-bottom:14px; }
  table { border-collapse:collapse; width:100%; margin:8px 0; font-size:0.87em; }
  th,td { border:1px solid #e2e8f0; padding:5px 8px; text-align:left; vertical-align:top; }
  th { background:#f1f5f9; font-weight:600; }
  th.sortable { cursor:pointer; user-select:none; white-space:nowrap; }
  th.sortable:hover { background:#e2e8f0; }
  th.sort-asc::after  { content:' ▲'; font-size:0.75em; color:#64748b; }
  th.sort-desc::after { content:' ▼'; font-size:0.75em; color:#64748b; }
  .num { text-align:right; font-variant-numeric:tabular-nums; }
  .good { color:#16a34a; }
  .bad  { color:#dc2626; }
  .neutral { color:#94a3b8; }
  .mono { font-family:ui-monospace,Consolas,monospace; font-size:0.82em; }
  .section { background:#fff; border-radius:6px; padding:14px; margin:10px 0;
             box-shadow:0 1px 2px rgba(0,0,0,0.06); }
  details { background:#fafbfd; border:1px solid #e2e8f0; border-radius:4px;
            margin:4px 0; }
  summary { padding:6px 10px; cursor:pointer; font-size:0.88em; }
  details > div, details > table { padding:6px 10px; border-top:1px solid #e2e8f0; }
  .fact-hit { color:#16a34a; }
  .fact-bl  { color:#dc2626; }
  .fact-miss { color:#94a3b8; font-style:italic; }
"""

_SORT_JS = """
<script>
(function(){
  function parseVal(td) {
    var t = td.dataset.val !== undefined ? td.dataset.val : td.innerText.trim();
    var n = parseFloat(t.replace(/[^0-9.\\-]/g, ''));
    return isNaN(n) ? t.toLowerCase() : n;
  }
  document.querySelectorAll('th.sortable').forEach(function(th) {
    th.addEventListener('click', function() {
      var table = th.closest('table');
      var idx = Array.from(th.parentNode.children).indexOf(th);
      var asc = th.classList.contains('sort-asc') ? false : true;
      table.querySelectorAll('th.sortable').forEach(function(h) {
        h.classList.remove('sort-asc','sort-desc');
      });
      th.classList.add(asc ? 'sort-asc' : 'sort-desc');
      var tbody = table.querySelector('tbody') || table;
      var rows = Array.from(tbody.querySelectorAll('tr')).filter(function(r){
        return r.querySelector('td');
      });
      rows.sort(function(a,b){
        var av = parseVal(a.children[idx]);
        var bv = parseVal(b.children[idx]);
        if (av < bv) return asc ? -1 : 1;
        if (av > bv) return asc ? 1 : -1;
        return 0;
      });
      rows.forEach(function(r){ tbody.appendChild(r); });
    });
  });
})();
</script>
"""


def _aggregate(results: list) -> dict:
    total_hits = sum(len(r.hits) for r in results)
    total_bl = sum(len(r.blacklist_hits) for r in results)
    total_emitted = sum(len(r.emitted_facts) for r in results)
    total_errors = sum(1 for r in results if r.error)
    total_cost = sum(r.cost_usd for r in results)
    total_lat = sum(r.latency_ms for r in results)
    total_prompt = sum(r.prompt_tokens for r in results)
    total_compl = sum(r.completion_tokens for r in results)
    n = len(results)
    return {
        "n": n,
        "hits": total_hits,
        "blacklist_hits": total_bl,
        "emitted": total_emitted,
        "errors": total_errors,
        "score": total_hits - total_bl,
        "cost": total_cost,
        "lat_avg": total_lat / n if n else 0,
        "prompt_tokens": total_prompt,
        "completion_tokens": total_compl,
    }


def generate_report(config: dict, results_by_model: dict, gt_by_id: dict, out_path: Path) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # GT ceiling per source + total
    ceiling_per_source = {sid: len(s.get("good_facts", []) or []) for sid, s in gt_by_id.items()}
    total_ceiling = sum(ceiling_per_source.values())

    # Max score per model for normalization
    max_score = max((_aggregate(v)["score"] for v in results_by_model.values()), default=0) or 1

    parts: list[str] = []
    parts.append(f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>Fact Bench</title>"
                 f"<style>{_CSS}</style></head><body>")
    parts.append("<h1>Fact-extraction bench</h1>")
    parts.append(f"<div class='meta'>Generated {now} · {len(results_by_model)} model(s) · "
                 f"{sum(len(v) for v in results_by_model.values())} calls · "
                 f"GT ceiling total: {total_ceiling} good facts across "
                 f"{len(gt_by_id)} sources</div>")

    # Summary
    parts.append("<div class='section'><h2>Summary (per model)</h2><table>")
    parts.append(
        "<tr>"
        "<th class='sortable'>Model</th>"
        "<th class='sortable num'>Sources</th>"
        "<th class='sortable num'>Hits</th>"
        "<th class='sortable num'>Blacklist</th>"
        "<th class='sortable num'>Emitted</th>"
        "<th class='sortable num'>Errors</th>"
        "<th class='sortable'>Score (normalized)</th>"
        "<th class='sortable'>Recall vs ceiling</th>"
        "<th class='sortable num'>Cost</th>"
        "<th class='sortable num'>Avg latency</th>"
        "<th class='sortable num'>Prompt tok</th>"
        "<th class='sortable num'>Compl tok</th>"
        "<th class='sortable num'>Cost/point</th>"
        "</tr>"
    )
    rows = sorted(
        ((lbl, _aggregate(v)) for lbl, v in results_by_model.items()),
        key=lambda x: -x[1]["score"],
    )
    for label, agg in rows:
        norm = 100.0 * agg["score"] / max_score
        recall = 100.0 * agg["hits"] / total_ceiling if total_ceiling else 0
        score_color = "#16a34a" if norm >= 80 else "#f59e0b" if norm >= 50 else "#dc2626"
        rc_color = "#16a34a" if recall >= 60 else "#f59e0b" if recall >= 40 else "#dc2626"
        cost_per_point = agg["cost"] / agg["score"] if agg["score"] > 0 else None
        cpp_str = f"${cost_per_point:.4f}" if cost_per_point is not None else "N/A"
        cpp_val = f"{cost_per_point:.6f}" if cost_per_point is not None else "9999"
        parts.append(
            "<tr>"
            f"<td class='mono'>{_esc(label)}</td>"
            f"<td class='num'>{agg['n']}</td>"
            f"<td class='num good'>{agg['hits']}</td>"
            f"<td class='num bad'>{agg['blacklist_hits']}</td>"
            f"<td class='num'>{agg['emitted']}</td>"
            f"<td class='num'>{agg['errors']}</td>"
            f"<td data-val='{agg['score']}'>{_bar(max(0,norm), score_color)} <b>{agg['score']}</b> ({norm:.0f}%)</td>"
            f"<td data-val='{recall:.2f}'>{_bar(recall, rc_color)} <b>{agg['hits']}/{total_ceiling}</b> ({recall:.0f}%)</td>"
            f"<td class='num' data-val='{agg['cost']:.6f}'>${agg['cost']:.4f}</td>"
            f"<td class='num' data-val='{agg['lat_avg']:.0f}'>{agg['lat_avg']:.0f}ms</td>"
            f"<td class='num'>{agg['prompt_tokens']:,}</td>"
            f"<td class='num'>{agg['completion_tokens']:,}</td>"
            f"<td class='num' data-val='{cpp_val}'>{cpp_str}</td>"
            "</tr>"
        )
    parts.append("</table></div>")

    # Per-model collapsible detail
    for label, results in results_by_model.items():
        agg = _aggregate(results)
        parts.append(
            f"<details class='section'><summary><b>{_esc(label)}</b> · "
            f"hits={agg['hits']} bl={agg['blacklist_hits']} score={agg['score']} · "
            f"${agg['cost']:.4f} · {agg['lat_avg']:.0f}ms</summary>"
        )
        for r in sorted(results, key=lambda x: x.source_id):
            ceiling = ceiling_per_source.get(r.source_id, 0)
            parts.append(
                f"<details><summary>src {r.source_id} · "
                f"{_esc(r.title[:80])} · hits {len(r.hits)}/{ceiling}"
                f" · bl {len(r.blacklist_hits)}"
                f" · emitted {len(r.emitted_facts)}"
                f"{' · ERROR' if r.error else ''}</summary>"
            )
            if r.error:
                parts.append(f"<div class='bad'>{_esc(r.error)}</div>")
            # Emitted facts with classification
            parts.append("<table>")
            parts.append("<tr><th>Emitted</th><th>Matched GT</th><th class='num'>Score</th></tr>")
            hit_lookup = {h[0]: (h[1], h[2], "hit") for h in r.hits}
            bl_lookup = {b[0]: (b[1], b[2], "bl") for b in r.blacklist_hits}
            for f in r.emitted_facts:
                if f in hit_lookup:
                    gt, sc, _ = hit_lookup[f]
                    parts.append(
                        f"<tr><td class='fact-hit'>✓ {_esc(f)}</td>"
                        f"<td class='mono'>{_esc(gt)}</td>"
                        f"<td class='num'>{sc:.3f}</td></tr>"
                    )
                elif f in bl_lookup:
                    gt, sc, _ = bl_lookup[f]
                    parts.append(
                        f"<tr><td class='fact-bl'>✗ {_esc(f)}</td>"
                        f"<td class='mono'>{_esc(gt)}</td>"
                        f"<td class='num'>{sc:.3f}</td></tr>"
                    )
                else:
                    parts.append(
                        f"<tr><td class='neutral'>{_esc(f)}</td>"
                        f"<td class='neutral'>(no-match)</td>"
                        f"<td class='num'>-</td></tr>"
                    )
            parts.append("</table>")
            # Missed GT
            if r.missed_gt:
                parts.append("<div><b>Missed GT:</b><ul>")
                for mg in r.missed_gt:
                    parts.append(f"<li class='fact-miss'>{_esc(mg)}</li>")
                parts.append("</ul></div>")
            parts.append("</details>")
        parts.append("</details>")

    parts.append(_SORT_JS)
    parts.append("</body></html>")
    out_path.write_text("\n".join(parts), encoding="utf-8")
    print(f"HTML report written: {out_path}")
