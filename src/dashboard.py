"""
Generates a single, self-contained HTML dashboard from the reconciliation
report. No server, no external dependencies, no API calls -- the report
data is embedded directly in the page as JSON, and a small JS lookup
(mirroring qa_agent.py's offline fallback logic) makes the "ask a
question" box work entirely in the browser.

For real, LLM-reasoned answers during the pitch, keep using
`python src/qa_agent.py "..."` in a terminal alongside this dashboard --
this page is the visual, always-works companion, not a replacement.

Usage:
    python src/dashboard.py
    -> writes output/dashboard.html
    -> open it in any browser
"""

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(ROOT, "output")


def main():
    report_path = os.path.join(OUTPUT_DIR, "reconciliation_report.json")
    if not os.path.exists(report_path):
        print("No report found. Run `python src/main.py` first.")
        return

    with open(report_path) as f:
        report = json.load(f)

    html = build_html(report)
    out_path = os.path.join(OUTPUT_DIR, "dashboard.html")
    with open(out_path, "w") as f:
        f.write(html)
    print(f"Dashboard written to {out_path} -- open it in a browser.")


def build_html(report):
    s = report["summary"]
    report_json = json.dumps(report)

    stage_rows = "".join(
        f"<tr><td>{stage}</td><td>{count}</td></tr>"
        for stage, count in sorted(s["matches_by_stage"].items(), key=lambda x: -x[1])
    )

    max_count = max(s["matches_by_stage"].values()) if s["matches_by_stage"] else 1
    bars = "".join(
        f'<div class="bar-row"><div class="bar-label">{stage}</div>'
        f'<div class="bar-track"><div class="bar-fill" style="width:{count/max_count*100:.0f}%"></div></div>'
        f'<div class="bar-count">{count}</div></div>'
        for stage, count in sorted(s["matches_by_stage"].items(), key=lambda x: -x[1])
    )

    exception_rows = "".join(
        f"<tr><td>{u['ledger_id']}</td><td>{u['reason']}</td></tr>"
        for u in report["unresolved_ledger"]
    ) or "<tr><td colspan=2>None — every ledger row was resolved.</td></tr>"

    sample_rows = "".join(
        f"<tr><td>{', '.join(m['ledger_ids'])}</td><td>{', '.join(m['gateway_ids'])}</td>"
        f"<td>{m['stage']}</td><td>{m['confidence']}</td><td>{m['reason']}</td></tr>"
        for m in report["matches"][:15]
    )

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>AI Finance Controller — Reconciliation Dashboard</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; max-width: 900px; margin: 40px auto; padding: 0 20px; color: #1a1a1a; }}
  h1 {{ font-size: 22px; font-weight: 600; }}
  h2 {{ font-size: 17px; font-weight: 600; margin-top: 36px; }}
  .cards {{ display: flex; gap: 16px; margin: 20px 0; flex-wrap: wrap; }}
  .card {{ background: #f7f7f5; border: 1px solid #e5e5e0; border-radius: 10px; padding: 16px 20px; min-width: 150px; }}
  .card .num {{ font-size: 26px; font-weight: 600; }}
  .card .label {{ font-size: 13px; color: #666; margin-top: 4px; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 10px; font-size: 14px; }}
  th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid #eee; }}
  th {{ background: #fafaf8; font-weight: 600; }}
  .bar-row {{ display: flex; align-items: center; gap: 10px; margin: 6px 0; font-size: 13px; }}
  .bar-label {{ width: 130px; flex-shrink: 0; }}
  .bar-track {{ flex: 1; background: #eee; border-radius: 4px; height: 18px; overflow: hidden; }}
  .bar-fill {{ background: #3c7a5f; height: 100%; }}
  .bar-count {{ width: 30px; text-align: right; }}
  #qa-box {{ margin-top: 12px; }}
  #qa-input {{ width: 70%; padding: 8px 10px; font-size: 14px; border: 1px solid #ccc; border-radius: 6px; }}
  #qa-ask {{ padding: 8px 16px; font-size: 14px; border-radius: 6px; border: none; background: #3c7a5f; color: white; cursor: pointer; }}
  #qa-answer {{ margin-top: 14px; padding: 12px 16px; background: #f7f7f5; border-radius: 8px; font-size: 14px; white-space: pre-wrap; min-height: 20px; }}
  .hint {{ font-size: 12px; color: #888; margin-top: 6px; }}
</style>
</head>
<body>

<h1>AI Finance Controller — Reconciliation Dashboard</h1>

<div class="cards">
  <div class="card"><div class="num">{s['match_rate_pct']}%</div><div class="label">Match rate</div></div>
  <div class="card"><div class="num">{s['matched_ledger_rows']}/{s['total_ledger_rows']}</div><div class="label">Ledger rows matched</div></div>
  <div class="card"><div class="num">{s['unresolved_ledger_count']}</div><div class="label">Unresolved</div></div>
</div>

<h2>Matches by stage</h2>
{bars}
<table><thead><tr><th>Stage</th><th>Count</th></tr></thead><tbody>{stage_rows}</tbody></table>

<h2>Unresolved / exceptions</h2>
<table><thead><tr><th>Ledger ID</th><th>Reason</th></tr></thead><tbody>{exception_rows}</tbody></table>

<h2>Sample resolved matches</h2>
<table><thead><tr><th>Ledger</th><th>Gateway</th><th>Stage</th><th>Confidence</th><th>Reason</th></tr></thead><tbody>{sample_rows}</tbody></table>

<h2>Ask about a specific order or settlement</h2>
<div id="qa-box">
  <input id="qa-input" type="text" placeholder="e.g. why wasn't ORD1005 settled?">
  <button id="qa-ask">Ask</button>
  <div class="hint">Runs fully in your browser using this exact report — no server, no API call. For real LLM-reasoned answers, run <code>python src/qa_agent.py "..."</code> in a terminal.</div>
  <div id="qa-answer"></div>
</div>

<script>
const report = {report_json};

function findIds(text) {{
  const matches = text.toUpperCase().match(/\\b(ORD\\d+|STL\\d+[A-Z\\-]*)\\b/g);
  return matches ? [...new Set(matches)] : [];
}}

function answer(question) {{
  const ids = findIds(question);
  if (ids.length === 0) {{
    const s = report.summary;
    return `No specific order/settlement ID found in your question. Overall: ${{s.match_rate_pct}}% match rate, ${{s.unresolved_ledger_count}} unresolved ledger rows out of ${{s.total_ledger_rows}}.`;
  }}
  const lines = [];
  for (const id of ids) {{
    const match = report.matches.find(m => m.ledger_ids.includes(id) || m.gateway_ids.includes(id));
    const exception = report.unresolved_ledger.find(u => u.ledger_id === id);
    if (match) {{
      lines.push(`${{id}} was matched via '${{match.stage}}' (confidence ${{match.confidence}}): ${{match.reason}}`);
    }} else if (exception) {{
      lines.push(`${{id}} is UNRESOLVED. Reason: ${{exception.reason}}`);
    }} else if (report.unresolved_gateway_ids && report.unresolved_gateway_ids.includes(id)) {{
      lines.push(`${{id}} is an unresolved gateway settlement row with no confirmed ledger match.`);
    }} else {{
      lines.push(`${{id}} was not found in this report.`);
    }}
  }}
  return lines.join("\\n");
}}

document.getElementById('qa-ask').addEventListener('click', () => {{
  const q = document.getElementById('qa-input').value;
  document.getElementById('qa-answer').textContent = q ? answer(q) : '';
}});
document.getElementById('qa-input').addEventListener('keydown', (e) => {{
  if (e.key === 'Enter') document.getElementById('qa-ask').click();
}});
</script>

</body>
</html>
"""


if __name__ == "__main__":
    main()