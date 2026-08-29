"""
Settlement Q&A agent.

Takes a natural-language question like "why wasn't ORD1005 settled?" and
answers it using ONLY the data already produced by the reconciliation
pipeline (reconciliation_report.json + the original CSVs). It never
guesses beyond what's in that data -- if the row isn't found, it says so.

This sits on top of the existing pipeline with zero new matching logic:
it's a thin, grounded lookup + explanation layer, reusing the exact same
"only reason over what you're shown" contract as llm_resolver.py.

Usage:
    python qa_agent.py "why wasn't ORD1005 settled?"
    python qa_agent.py                      # interactive mode
"""

import csv
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
OUTPUT_DIR = os.path.join(ROOT, "output")
GEMINI_MODEL = "gemini-3.5-flash-lite"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

ID_PATTERN = re.compile(r"\b(ORD\d+|STL\d+[A-Za-z\-]*)\b", re.IGNORECASE)

SYSTEM_PROMPT = """You are a settlement Q&A assistant for a finance-ops \
reconciliation tool. You will be given a user's question plus the exact \
JSON data relevant to it (ledger rows, gateway rows, match/exception \
records).

Answer in 2-4 plain-language sentences using ONLY the data provided.
Your answer MUST cite at least one concrete fact from the context by
value -- an actual amount, date, reference string, or confidence score --
not just a restated status. A vague answer like "it resulted in an
exception" is not acceptable; say what specifically didn't line up
(e.g. the exact amount difference, which reference strings were compared,
how many days apart the dates were).

Never invent facts, IDs, amounts, or dates not present in the context.
If the context doesn't contain enough information to answer, say so
plainly instead of guessing."""


def load_data():
    def load_csv(path):
        with open(path, newline="") as f:
            return list(csv.DictReader(f))

    ledger = {r["order_id"]: r for r in load_csv(os.path.join(DATA_DIR, "internal_ledger.csv"))}
    gateway = {r["settlement_id"]: r for r in load_csv(os.path.join(DATA_DIR, "gateway_settlement.csv"))}

    report_path = os.path.join(OUTPUT_DIR, "reconciliation_report.json")
    with open(report_path) as f:
        report = json.load(f)

    return ledger, gateway, report


def build_context(question, ledger, gateway, report):
    """Pulls together every piece of the report that's actually relevant
    to the IDs mentioned in the question, so the LLM only sees grounded
    facts -- never the entire dataset."""
    ids = {m.group(0).upper() for m in ID_PATTERN.finditer(question)}
    context = {"ledger_rows": {}, "gateway_rows": {}, "match_records": [], "exception_records": []}

    if not ids:
        # No specific ID mentioned -- fall back to summary-level context only
        context["summary"] = report["summary"]
        return context, ids

    for id_ in ids:
        if id_ in ledger:
            context["ledger_rows"][id_] = ledger[id_]
        if id_ in gateway:
            context["gateway_rows"][id_] = gateway[id_]

    for m in report["matches"]:
        if any(i in ids for i in m["ledger_ids"]) or any(i in ids for i in m["gateway_ids"]):
            context["match_records"].append(m)

    for u in report["unresolved_ledger"]:
        if u["ledger_id"] in ids:
            context["exception_records"].append(u)
            # Also include the actual candidate gateway rows that were
            # compared and rejected, from the same merchant, so the model
            # has real numbers to cite -- not just the final verdict.
            ledger_row = ledger.get(u["ledger_id"])
            if ledger_row:
                same_merchant_candidates = {
                    gid: gateway[gid] for gid in report["unresolved_gateway_ids"]
                    if gid in gateway and gateway[gid]["merchant"] == ledger_row["merchant"]
                }
                if same_merchant_candidates:
                    context.setdefault("rejected_candidate_gateway_rows", {}).update(same_merchant_candidates)

    unresolved_gateway_hits = [g for g in report["unresolved_gateway_ids"] if g in ids]
    if unresolved_gateway_hits:
        context["unresolved_gateway_ids"] = unresolved_gateway_hits

    return context, ids


def _call_llm(question, context):
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return None

    user_content = json.dumps({"question": question, "context": context}, indent=2)

    body = json.dumps({
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"parts": [{"text": user_content}]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 1024},
    }).encode()

    req = urllib.request.Request(
        f"{GEMINI_URL}?key={api_key}",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts).strip()
    except Exception as e:
        print(f"  [qa_agent] API call failed, falling back to templated answer: {e}")
        return None


def _templated_fallback(question, context, ids):
    """Deterministic, still-grounded answer used when no API key is set,
    so the demo always works offline."""
    if not ids:
        s = context.get("summary", {})
        return (f"[offline template] No specific order/settlement ID found in your question. "
                 f"Overall: {s.get('match_rate_pct', '?')}% match rate, "
                 f"{s.get('unresolved_ledger_count', '?')} unresolved ledger rows out of "
                 f"{s.get('total_ledger_rows', '?')}.")

    lines = []
    for id_ in ids:
        if context["match_records"]:
            for m in context["match_records"]:
                lines.append(f"[offline template] {id_} was matched via '{m['stage']}' "
                              f"(confidence {m['confidence']}): {m['reason']}")
        elif context["exception_records"]:
            for e in context["exception_records"]:
                lines.append(f"[offline template] {id_} is UNRESOLVED. Reason: {e['reason']}")
        elif id_ in context["ledger_rows"] or id_ in context["gateway_rows"]:
            lines.append(f"[offline template] {id_} exists in the data but has no recorded "
                          f"match or exception entry -- check it was included in a pipeline run.")
        else:
            lines.append(f"[offline template] {id_} was not found in the ledger, gateway, "
                          f"or report data.")
    # Defensive dedupe: never show the same line twice, regardless of cause.
    deduped = list(dict.fromkeys(lines))
    return "\n".join(deduped)


def answer(question):
    ledger, gateway, report = load_data()
    context, ids = build_context(question, ledger, gateway, report)

    llm_answer = _call_llm(question, context)
    if llm_answer:
        return llm_answer
    return _templated_fallback(question, context, ids)


def main():
    if len(sys.argv) > 1:
        question = " ".join(sys.argv[1:])
        print(answer(question))
        return

    print("Settlement Q&A agent. Ask about any order (ORDxxxx) or settlement (STLxxxx). Ctrl+C to quit.\n")
    while True:
        try:
            q = input("> ")
        except (EOFError, KeyboardInterrupt):
            break
        if not q.strip():
            continue
        print(answer(q), "\n")


if __name__ == "__main__":
    main()
