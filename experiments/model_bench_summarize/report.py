"""HTML report for summarization bench."""

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
  .mono { font-family:ui-monospace,Consolas,monospace; font-size:0.82em; }
  .good { color:#16a34a; }
  .bad  { color:#dc2626; }
  .neutral { color:#94a3b8; }
  .section { background:#fff; border-radius:6px; padding:14px; margin:10px 0;
             box-shadow:0 1px 2px rgba(0,0,0,0.06); }
  details { background:#fafbfd; border:1px solid #e2e8f0; border-radius:4px; margin:4px 0; }
  summary { padding:6px 10px; cursor:pointer; font-size:0.88em; }
  details > div, details > table { padding:6px 10px; border-top:1px solid #e2e8f0; }
  .summary-text { padding:10px; background:#f8fafc; border-left:3px solid #cbd5e1;
                  margin:6px 0; white-space:pre-wrap; font-size:0.9em; line-height:1.5; }
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
    valid = [r for r in results if r.error is None]
    n = len(results)
    cov_mean = sum(r.coverage_mean for r in valid) / len(valid) if valid else 0
    cov_min = min((r.coverage_min for r in valid), default=0)
    cov_max = max((r.coverage_max for r in valid), default=0)
    total_cost = sum(r.cost_usd for r in results)
    total_lat = sum(r.latency_ms for r in results)
    total_pt = sum(r.prompt_tokens for r in results)
    total_ct = sum(r.completion_tokens for r in results)
    errors = sum(1 for r in results if r.error)
    avg_words = sum(len(r.summary.split()) for r in valid) / len(valid) if valid else 0
    # Tokens per second — output tokens / (latency / 1000)
    tps_vals = [
        r.completion_tokens / (r.latency_ms / 1000)
        for r in valid if r.latency_ms > 0 and r.completion_tokens > 0
    ]
    tps = sum(tps_vals) / len(tps_vals) if tps_vals else 0
    return {
        "n": n, "valid": len(valid), "errors": errors,
        "cov_mean": cov_mean, "cov_min": cov_min, "cov_max": cov_max,
        "cost": total_cost, "lat_avg": total_lat / n if n else 0,
        "prompt_tokens": total_pt, "completion_tokens": total_ct,
        "avg_words": avg_words, "tps": tps,
    }


def generate_report(config: dict, results_by_model: dict, seeds: list, out_path: Path) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    parts: list[str] = []
    parts.append(f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>Summarize Bench</title>"
                 f"<style>{_CSS}</style></head><body>")
    parts.append("<h1>Summarization bench — plain text, semantic coverage scored</h1>")
    parts.append(f"<div class='meta'>Generated {now} · {len(results_by_model)} model(s) · "
                 f"{len(seeds)} seed(s) × 20 facts · "
                 f"score = mean cosine(summary, fact) — higher is denser coverage</div>")

    parts.append("<div class='section'><h2>Summary (per model)</h2><table>")
    parts.append(
        "<tr>"
        "<th class='sortable'>Model</th>"
        "<th class='sortable num'>Seeds</th>"
        "<th class='sortable num'>Errors</th>"
        "<th class='sortable'>Coverage (mean cos)</th>"
        "<th class='sortable num'>Min cos</th>"
        "<th class='sortable num'>Max cos</th>"
        "<th class='sortable num'>Avg words</th>"
        "<th class='sortable num'>Cost</th>"
        "<th class='sortable num'>Avg latency</th>"
        "<th class='sortable num'>Tokens/s</th>"
        "<th class='sortable num'>Prompt tok</th>"
        "<th class='sortable num'>Compl tok</th>"
        "<th class='sortable num'>Cost/cov-pt</th>"
        "</tr>"
    )

    rows = sorted(
        ((lbl, _aggregate(v)) for lbl, v in results_by_model.items()),
        key=lambda x: -x[1]["cov_mean"],
    )
    for label, agg in rows:
        cov_pct = 100.0 * agg["cov_mean"]
        cov_color = "#16a34a" if cov_pct >= 80 else "#f59e0b" if cov_pct >= 70 else "#dc2626"
        cost_per_pt = agg["cost"] / agg["cov_mean"] if agg["cov_mean"] > 0 else None
        cpp_str = f"${cost_per_pt:.4f}" if cost_per_pt is not None else "N/A"
        cpp_val = f"{cost_per_pt:.6f}" if cost_per_pt is not None else "9999"
        parts.append(
            "<tr>"
            f"<td class='mono'>{_esc(label)}</td>"
            f"<td class='num'>{agg['n']}</td>"
            f"<td class='num'>{agg['errors']}</td>"
            f"<td data-val='{agg['cov_mean']:.4f}'>{_bar(cov_pct, cov_color)} <b>{agg['cov_mean']:.3f}</b></td>"
            f"<td class='num'>{agg['cov_min']:.3f}</td>"
            f"<td class='num'>{agg['cov_max']:.3f}</td>"
            f"<td class='num'>{agg['avg_words']:.0f}</td>"
            f"<td class='num' data-val='{agg['cost']:.6f}'>${agg['cost']:.4f}</td>"
            f"<td class='num' data-val='{agg['lat_avg']:.0f}'>{agg['lat_avg']:.0f}ms</td>"
            f"<td class='num'>{agg['tps']:.0f}</td>"
            f"<td class='num'>{agg['prompt_tokens']:,}</td>"
            f"<td class='num'>{agg['completion_tokens']:,}</td>"
            f"<td class='num' data-val='{cpp_val}'>{cpp_str}</td>"
            "</tr>"
        )
    parts.append("</table></div>")

    # Per-seed breakdown
    parts.append("<div class='section'><h2>Coverage by seed</h2><table>")
    parts.append("<tr><th class='sortable'>Model</th>" +
                 "".join(f"<th class='sortable num'>{_esc(s['seed'])}</th>" for s in seeds) +
                 "</tr>")
    for label, results in results_by_model.items():
        by_id = {r.seed_id: r for r in results}
        parts.append(f"<tr><td class='mono'>{_esc(label)}</td>")
        for s in seeds:
            r = by_id.get(int(s["id"]))
            if r and r.error is None:
                color = "#16a34a" if r.coverage_mean >= 0.80 else "#f59e0b" if r.coverage_mean >= 0.70 else "#dc2626"
                parts.append(f"<td class='num' data-val='{r.coverage_mean:.4f}' "
                             f"style='color:{color}'>{r.coverage_mean:.3f}</td>")
            else:
                parts.append(f"<td class='num neutral'>—</td>")
        parts.append("</tr>")
    parts.append("</table></div>")

    # Per-model detail
    for label, results in results_by_model.items():
        agg = _aggregate(results)
        parts.append(
            f"<details class='section'><summary><b>{_esc(label)}</b> · "
            f"cov={agg['cov_mean']:.3f} · ${agg['cost']:.4f} · "
            f"{agg['lat_avg']:.0f}ms · {agg['tps']:.0f}tps</summary>"
        )
        for r in sorted(results, key=lambda x: x.seed_id):
            if r.error:
                parts.append(f"<details><summary>seed {r.seed_id} · {_esc(r.seed)} · ERROR</summary>"
                             f"<div class='bad'>{_esc(r.error)}</div></details>")
                continue
            parts.append(
                f"<details><summary>seed {r.seed_id} · <b>{_esc(r.seed)}</b> · "
                f"cov={r.coverage_mean:.3f} (min {r.coverage_min:.3f}, max {r.coverage_max:.3f}) · "
                f"{len(r.summary.split())} words</summary>"
                f"<div class='summary-text'>{_esc(r.summary)}</div>"
            )
            parts.append("<table>")
            parts.append("<tr><th>Fact</th><th class='num'>cos(summary)</th></tr>")
            pairs = sorted(zip(r.facts, r.per_fact_cos), key=lambda p: -p[1])
            for f, c in pairs:
                color = "#16a34a" if c >= 0.80 else "#f59e0b" if c >= 0.70 else "#dc2626"
                parts.append(f"<tr><td>{_esc(f)}</td>"
                             f"<td class='num' style='color:{color}'>{c:.3f}</td></tr>")
            parts.append("</table></details>")
        parts.append("</details>")

    parts.append(_SORT_JS)
    parts.append("</body></html>")
    out_path.write_text("\n".join(parts), encoding="utf-8")
    print(f"HTML report written: {out_path}")
