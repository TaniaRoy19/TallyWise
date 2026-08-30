from dotenv import load_dotenv
load_dotenv()
import csv
import json
import os
import sys
from reconcile import reconcile
from llm_resolver import resolve_unresolved

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
OUTPUT_DIR = os.path.join(ROOT, "output")
LEDGER_PATH = os.path.join(DATA_DIR, "internal_ledger.csv")
GATEWAY_PATH = os.path.join(DATA_DIR, "gateway_settlement.csv")


def load_csv(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def ensure_data_exists():
    """Foolproof guard: if someone runs main.py without generating data
    first, generate it automatically instead of crashing."""
    if os.path.exists(LEDGER_PATH) and os.path.exists(GATEWAY_PATH):
        return
    print("No data found in data/ — generating synthetic dataset automatically...")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import data_gen
    data_gen.main()


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    ensure_data_exists()

    ledger = load_csv(LEDGER_PATH)
    gateway = load_csv(os.path.join(DATA_DIR, "gateway_settlement.csv"))

    report, ledger_pool, gateway_pool = reconcile(ledger, gateway)

    print(f"Stage 1+2 (rule-based) resolved {len(report.matches)} of {len(ledger)} ledger rows.")
    print(f"Escalating {len(report.unresolved_ledger)} unresolved ledger rows to LLM stage...")

    llm_matches, still_unresolved_ledger, still_unresolved_gateway = resolve_unresolved(
        ledger_pool, gateway_pool, report.unresolved_ledger, report.unresolved_gateway
    )

    all_matches = [m.__dict__ for m in report.matches] + llm_matches
    total_ledger = len(ledger)
    matched_ledger_count = sum(len(m["ledger_ids"]) for m in all_matches)
    match_rate = matched_ledger_count / total_ledger if total_ledger else 0

    stage_counts = {}
    for m in all_matches:
        stage_counts[m["stage"]] = stage_counts.get(m["stage"], 0) + 1

    cause_counts = {}
    for u in still_unresolved_ledger:
        c = u.get("likely_cause_label", "Unclear cause")
        cause_counts[c] = cause_counts.get(c, 0) + 1

    result = {
        "summary": {
            "total_ledger_rows": total_ledger,
            "total_gateway_rows": len(gateway),
            "matched_ledger_rows": matched_ledger_count,
            "match_rate_pct": round(match_rate * 100, 1),
            "matches_by_stage": stage_counts,
            "unresolved_ledger_count": len(still_unresolved_ledger),
            "unresolved_gateway_count": len(still_unresolved_gateway),
            "unresolved_by_cause": cause_counts,
        },
        "matches": all_matches,
        "unresolved_ledger": still_unresolved_ledger,
        "unresolved_gateway_ids": still_unresolved_gateway,
    }

    with open(os.path.join(OUTPUT_DIR, "reconciliation_report.json"), "w") as f:
        json.dump(result, f, indent=2)

    write_markdown_report(result)

    print("\n=== SUMMARY ===")
    print(json.dumps(result["summary"], indent=2))
    print("\nFull report: output/reconciliation_report.json")
    print("Readable report: output/REPORT.md")


def write_markdown_report(result):
    s = result["summary"]
    lines = []
    lines.append("# Reconciliation Report\n")
    lines.append(f"**Match rate: {s['match_rate_pct']}%** "
                  f"({s['matched_ledger_rows']} / {s['total_ledger_rows']} ledger rows matched)\n")
    lines.append("## Matches by resolution stage\n")
    lines.append("| Stage | Count |")
    lines.append("|---|---|")
    for stage, count in sorted(s["matches_by_stage"].items(), key=lambda x: -x[1]):
        lines.append(f"| {stage} | {count} |")
    lines.append("")
    lines.append(f"## Unresolved ({s['unresolved_ledger_count']} ledger rows, "
                  f"{s['unresolved_gateway_count']} gateway rows)\n")
    if s.get("unresolved_by_cause"):
        lines.append("**By likely cause:**\n")
        for cause, count in sorted(s["unresolved_by_cause"].items(), key=lambda x: -x[1]):
            lines.append(f"- {cause}: {count}")
        lines.append("")
    if result["unresolved_ledger"]:
        lines.append("| Ledger ID | Likely cause | Reason the system could not resolve it |")
        lines.append("|---|---|---|")
        for u in result["unresolved_ledger"]:
            lines.append(f"| {u['ledger_id']} | {u.get('likely_cause_label', 'Unclear cause')} | {u['reason']} |")
    else:
        lines.append("_None \u2014 every ledger row was resolved._")
    lines.append("")
    lines.append("## Sample resolved matches (with reasoning)\n")
    lines.append("| Ledger | Gateway | Stage | Confidence | Reason |")
    lines.append("|---|---|---|---|---|")
    for m in result["matches"][:15]:
        lines.append(f"| {', '.join(m['ledger_ids'])} | {', '.join(m['gateway_ids'])} | "
                      f"{m['stage']} | {m['confidence']} | {m['reason']} |")

    with open(os.path.join(OUTPUT_DIR, "REPORT.md"), "w") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main()
