"""HTML report for the model bench.

Renders per-model, per-task summary tables + inline CSS bar charts for
accuracy, cost, and latency. Self-contained HTML, no external deps.
"""

from __future__ import annotations

import html as html_module
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path


def _esc(s: str) -> str:
    return html_module.escape(str(s) if s is not None else "")


def _bar(pct: float, color: str, width_px: int = 160) -> str:
    pct = max(0.0, min(100.0, pct))
    return (
        f'<div style="background:#f1f5f9;width:{width_px}px;'
        f'border-radius:3px;display:inline-block;vertical-align:middle">'
        f'<div style="width:{pct:.1f}%;background:{color};'
        f'height:10px;border-radius:3px"></div></div>'
    )


def _aggregate(results: list) -> dict:
    # Only curated items (correct is bool) count for accuracy. Uncurated
    # pool items (correct is None) contribute score but not correct/total
    # for accuracy%.
    judged = [r for r in results if r.correct is not None]
    correct = sum(1 for r in judged if r.correct)
    errors = sum(1 for r in results if r.error)
    total_score = sum(getattr(r, "score", 0.0) for r in results)
    total = len(results)
    n_judged = len(judged)
    total_prompt = sum(r.prompt_tokens for r in results)
    total_compl = sum(r.completion_tokens for r in results)
    total_cost = sum(r.cost_usd for r in results)
    total_lat = sum(r.latency_ms for r in results)
    return {
        "n": total,
        "n_judged": n_judged,
        "correct": correct,
        "errors": errors,
        "accuracy": 100.0 * correct / n_judged if n_judged else 0.0,
        "score": total_score,
        "prompt_tokens": total_prompt,
        "completion_tokens": total_compl,
        "cost_usd": total_cost,
        "latency_ms": total_lat,
        "avg_latency_ms": total_lat / total if total else 0,
    }


def _by_task(results: list) -> dict[str, list]:
    out: dict[str, list] = {}
    for r in results:
        out.setdefault(r.task, []).append(r)
    return out


_CSS = """
  * { box-sizing:border-box; margin:0; padding:0; }
  body { font-family:-apple-system,BlinkMacSystemFont,Roboto,sans-serif;
         max-width:1600px; margin:0 auto; padding:20px; background:#f8fafc; color:#1e293b; }
  h1 { font-size:1.5em; margin-bottom:4px; }
  h2 { font-size:1.2em; margin:22px 0 8px; border-bottom:1px solid #e2e8f0; padding-bottom:4px; }
  h3 { font-size:1em; margin:12px 0 6px; color:#334155; }
  .meta { color:#64748b; font-size:0.82em; margin-bottom:14px; }
  table { border-collapse:collapse; width:100%; margin:8px 0; font-size:0.87em; }
  th,td { border:1px solid #e2e8f0; padding:5px 8px; text-align:left; vertical-align:middle; }
  th { background:#f1f5f9; font-weight:600; }
  tr:hover td { background:#f8fafc; }
  .num { text-align:right; font-variant-numeric:tabular-nums; }
  .neutral { color:#94a3b8; }
  .mono { font-family:ui-monospace,Consolas,monospace; font-size:0.82em; }
  .good { color:#16a34a; }
  .bad  { color:#dc2626; }
  .section { background:#fff; border-radius:6px; padding:14px; margin:10px 0;
             box-shadow:0 1px 2px rgba(0,0,0,0.06); }
  details { background:#fafbfd; border:1px solid #e2e8f0; border-radius:4px;
            margin:4px 0; }
  details > summary { padding:6px 10px; cursor:pointer; font-size:0.88em; }
  details > div { padding:6px 10px; border-top:1px solid #e2e8f0; }
  .pill { display:inline-block; padding:1px 6px; border-radius:8px;
          font-size:0.75em; font-family:ui-monospace,Consolas,monospace; }
  .err  { color:#dc2626; font-size:0.78em; }
"""


def generate_report(config: dict, results_by_model: dict[str, list], out_path: Path) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Overall grand totals for scaling bars
    max_cost = max((_aggregate(v)["cost_usd"] for v in results_by_model.values()), default=0.0) or 1e-9
    max_lat  = max((_aggregate(v)["avg_latency_ms"] for v in results_by_model.values()), default=0.0) or 1e-9

    parts: list[str] = []
    parts.append(f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>Model Bench</title>"
                 f"<style>{_CSS}</style></head><body>")
    parts.append("<h1>Model Bench — alias_gen / shell_classify / suggest_disambig</h1>")
    parts.append(f"<div class='meta'>Generated {now} · "
                 f"{len(results_by_model)} model(s) · "
                 f"{sum(len(v) for v in results_by_model.values())} total calls</div>")

    # Max score across models for normalization
    max_score = max((_aggregate(v)["score"] for v in results_by_model.values()), default=0.0) or 1.0

    # ── Overall summary table ─────────────────────────────────────
    parts.append("<div class='section'><h2>Summary (per model)</h2><table>")
    parts.append(
        "<tr>"
        "<th>Model</th>"
        "<th class='num'>N</th>"
        "<th class='num'>Correct</th>"
        "<th class='num'>Errors</th>"
        "<th>Accuracy</th>"
        "<th>Score (normalized)</th>"
        "<th>Cost</th>"
        "<th>Avg latency</th>"
        "<th class='num'>Prompt tok</th>"
        "<th class='num'>Compl tok</th>"
        "</tr>"
    )
    rows_for_sort = []
    for label, results in results_by_model.items():
        agg = _aggregate(results)
        rows_for_sort.append((label, agg))
    rows_for_sort.sort(key=lambda x: -x[1]["score"])
    for label, agg in rows_for_sort:
        acc_color = "#16a34a" if agg["accuracy"] >= 80 else "#f59e0b" if agg["accuracy"] >= 60 else "#dc2626"
        norm = 100.0 * agg["score"] / max_score
        score_color = "#16a34a" if norm >= 80 else "#f59e0b" if norm >= 60 else "#dc2626"
        cost_pct = 100.0 * agg["cost_usd"] / max_cost
        lat_pct = 100.0 * agg["avg_latency_ms"] / max_lat
        parts.append(
            "<tr>"
            f"<td class='mono'>{_esc(label)}</td>"
            f"<td class='num'>{agg['n']}</td>"
            f"<td class='num good'>{agg['correct']}</td>"
            f"<td class='num'>{agg['errors']}</td>"
            f"<td>{_bar(agg['accuracy'], acc_color)} <b>{agg['accuracy']:.1f}%</b></td>"
            f"<td>{_bar(max(0, norm), score_color)} <b>{agg['score']:.1f}</b> ({norm:.0f}%)</td>"
            f"<td>{_bar(cost_pct, '#0ea5e9', 80)} ${agg['cost_usd']:.4f}</td>"
            f"<td>{_bar(lat_pct, '#f59e0b', 80)} {agg['avg_latency_ms']:.0f}ms</td>"
            f"<td class='num'>{agg['prompt_tokens']:,}</td>"
            f"<td class='num'>{agg['completion_tokens']:,}</td>"
            "</tr>"
        )
    parts.append("</table></div>")

    # ── Per-task breakdown (binary accuracy + score-based) ──────
    tasks = config.get("tasks", [])

    # Max score per task for normalization
    max_task_score: dict[str, float] = {}
    for t in tasks:
        max_task_score[t] = 0.0
        for _, results in results_by_model.items():
            sub = [r for r in results if r.task == t]
            s = sum(getattr(r, "score", 0.0) for r in sub)
            if s > max_task_score[t]:
                max_task_score[t] = s
        if max_task_score[t] == 0:
            max_task_score[t] = 1.0

    parts.append("<div class='section'><h2>Per-task — binary accuracy</h2><table>")
    parts.append("<tr><th>Model</th>" + "".join(f"<th>{_esc(t)}</th>" for t in tasks) + "</tr>")
    for label, results in results_by_model.items():
        by_t = _by_task(results)
        parts.append(f"<tr><td class='mono'>{_esc(label)}</td>")
        for t in tasks:
            sub = by_t.get(t, [])
            agg = _aggregate(sub)
            color = "#16a34a" if agg["accuracy"] >= 80 else "#f59e0b" if agg["accuracy"] >= 60 else "#dc2626"
            parts.append(
                f"<td>{_bar(agg['accuracy'], color, 110)} "
                f"<b>{agg['correct']}/{agg['n']}</b> ({agg['accuracy']:.0f}%)</td>"
            )
        parts.append("</tr>")
    parts.append("</table></div>")

    parts.append("<div class='section'><h2>Per-task — score (+1 per valid hit, −1 per blacklist)</h2><table>")
    parts.append("<tr><th>Model</th>" + "".join(f"<th>{_esc(t)}</th>" for t in tasks) + "</tr>")
    for label, results in results_by_model.items():
        by_t = _by_task(results)
        parts.append(f"<tr><td class='mono'>{_esc(label)}</td>")
        for t in tasks:
            sub = by_t.get(t, [])
            score = sum(getattr(r, "score", 0.0) for r in sub)
            norm = 100.0 * score / max_task_score[t]
            color = "#16a34a" if norm >= 80 else "#f59e0b" if norm >= 50 else "#dc2626"
            parts.append(
                f"<td>{_bar(max(0, norm), color, 110)} "
                f"<b>{score:.1f}</b> / {max_task_score[t]:.1f} ({norm:.0f}%)</td>"
            )
        parts.append("</tr>")
    parts.append("</table></div>")

    # ── Per-model detail (each model + each task collapsed) ──────
    for label, results in results_by_model.items():
        by_t = _by_task(results)
        agg_all = _aggregate(results)
        parts.append(
            f"<details class='section'><summary style='cursor:pointer;font-size:1.1em'>"
            f"<b>Model: <span class='mono'>{_esc(label)}</span></b> · "
            f"{agg_all['correct']}/{agg_all['n']} correct · "
            f"score={agg_all['score']:.1f} · ${agg_all['cost_usd']:.4f} · "
            f"{agg_all['avg_latency_ms']:.0f}ms/call</summary>"
        )
        for t in tasks:
            sub = by_t.get(t, [])
            if not sub:
                continue
            agg = _aggregate(sub)
            parts.append(
                f"<details><summary style='cursor:pointer;margin:6px 0;font-weight:600'>"
                f"{_esc(t)} — {agg['correct']}/{agg['n']} ({agg['accuracy']:.0f}%)"
                f" · score={agg['score']:.1f} · {agg['errors']} errors · ${agg['cost_usd']:.5f}"
                f" · avg {agg['avg_latency_ms']:.0f}ms</summary>"
            )
            parts.append("<table>")
            parts.append(
                "<tr><th>Name</th><th>Expected</th><th>Got</th>"
                "<th class='num'>Tokens</th><th class='num'>Cost</th>"
                "<th class='num'>Latency</th><th>OK?</th></tr>"
            )
            for r in sorted(sub, key=lambda x: x.name.lower()):
                got_str = json._dumps(r.response) if False else (str(r.response) if r.response else "")
                import json as _j
                got_str = _j.dumps(r.response) if r.response else ""
                exp_str = _j.dumps(r.expected)
                ok_html = "<span class='good'>✓</span>" if r.correct else (
                    f"<span class='err'>{_esc(r.error)}</span>" if r.error else "<span class='bad'>✗</span>"
                )
                parts.append(
                    "<tr>"
                    f"<td><b>{_esc(r.name)}</b></td>"
                    f"<td class='mono'>{_esc(exp_str[:120])}</td>"
                    f"<td class='mono'>{_esc(got_str[:160])}</td>"
                    f"<td class='num'>{r.prompt_tokens}→{r.completion_tokens}</td>"
                    f"<td class='num'>${r.cost_usd:.5f}</td>"
                    f"<td class='num'>{r.latency_ms}ms</td>"
                    f"<td>{ok_html}</td>"
                    "</tr>"
                )
            parts.append("</table></details>")
        parts.append("</details>")

    parts.append("</body></html>")
    out_path.write_text("\n".join(parts), encoding="utf-8")
    print(f"HTML report written: {out_path}")
