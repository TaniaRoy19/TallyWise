"""
Stage 3: LLM-assisted resolution.

Rule-based matching (Stage 1 & 2) handles the clean and moderately-messy
cases. Whatever is left is genuinely ambiguous -- e.g. duplicate rows that
look identical, orphan rows with no plausible counterpart, or near-miss
amounts that don't fit a known pattern (fee, split, drift).

For each unresolved ledger row, we give the LLM the row plus every
still-unresolved gateway row from the same merchant, and ask it to either:
  - propose the single best match with a confidence score and reason, or
  - state that it cannot responsibly resolve it, and say why -- and, for
    the "why", categorize the likely real-world cause so a human reviewer
    knows what kind of follow-up action is needed.

This keeps the LLM in a narrow, auditable role: it never invents data, it
only reasons over the exact rows it's shown, and every decision is logged
with its stated justification so a human can review it.

Uses Google's Gemini API (free tier, no billing required) via
GOOGLE_API_KEY. Falls back to a deterministic heuristic if no key is set.
"""

import json
import os

GEMINI_MODEL = "gemini-3.5-flash-lite"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

# Valid values for "likely_cause" when a row can't be resolved -- lets a
# human reviewer see at a glance what kind of follow-up each exception
# needs, instead of one flat undifferentiated exception list.
CAUSE_LABELS = {
    "missing_settlement": "Likely missing settlement",
    "data_entry_error": "Likely data-entry error",
    "ambiguous_duplicate": "Ambiguous / multiple candidates",
    "other": "Unclear cause",
}

SYSTEM_PROMPT = """You are a financial reconciliation assistant. You will be \
shown one unmatched ledger (internal order) row and a short list of \
candidate unmatched gateway (settlement) rows from the same merchant. \
Decide whether exactly one gateway row is the true counterpart of the \
ledger row.

Rules:
- Only use the data given. Never invent reference numbers, amounts, or dates.
- A duplicate-looking pair is NOT automatically a match -- say so if you \
can't tell which one (if any) is the real counterpart.
- If no candidate is a plausible match, say so, and classify WHY using
likely_cause:
  - "missing_settlement": there are no candidates at all, or none are even
    remotely close on amount/reference/date -- suggests the settlement
    never happened (needs escalation to the payment gateway).
  - "data_entry_error": exactly one candidate is close but not exact (e.g.
    a small amount gap, a near-matching reference) -- suggests a human
    should eyeball that one specific field.
  - "ambiguous_duplicate": two or more candidates are similarly plausible
    and you cannot confidently pick one -- suggests manual review to
    choose between them.
  - "other": doesn't fit the above.
- If you DO find a match, omit likely_cause (or set it to null).
- Respond ONLY with JSON, no other text, in exactly this shape:
{"match_settlement_id": "<id or null>", "confidence": <0.0-1.0>, "reason": "<one sentence>", "likely_cause": "<one of the labels above, or null if matched>"}
"""


def _call_llm(ledger_row, candidates):
    import urllib.request

    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return None  # caller falls back to heuristic

    user_content = json.dumps({
        "ledger_row": ledger_row,
        "candidate_gateway_rows": candidates,
    }, indent=2)

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
        text = "".join(p.get("text", "") for p in parts)
        text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            text = text[start:end + 1]
        return json.loads(text)
    except Exception as e:
        print(f"  [llm_resolver] API call failed, falling back to heuristic: {e}")
        return None


def _heuristic_fallback(ledger_row, candidates):
    """
    Used when no GOOGLE_API_KEY is set (e.g. first run / offline demo).
    Mimics the same narrow reasoning contract as the LLM -- including the
    likely_cause classification -- so the pipeline is fully runnable out
    of the box, and is clearly labeled as such in output.
    """
    if not candidates:
        return {"match_settlement_id": None, "confidence": 0.0,
                 "reason": "No candidate settlement rows from this merchant remain unmatched.",
                 "likely_cause": "missing_settlement"}
    if len(candidates) == 1:
        c = candidates[0]
        if abs(float(c["amount"]) - float(ledger_row["amount"])) < 0.01:
            return {"match_settlement_id": c["settlement_id"], "confidence": 0.6,
                     "reason": "[heuristic-fallback, no API key set] Only remaining candidate and amount matches exactly.",
                     "likely_cause": None}
        return {"match_settlement_id": None, "confidence": 0.0,
                 "reason": "[heuristic-fallback] Only candidate present but amount does not match closely enough.",
                 "likely_cause": "data_entry_error"}
    return {"match_settlement_id": None, "confidence": 0.0,
             "reason": f"[heuristic-fallback] {len(candidates)} equally plausible candidates; "
                        f"cannot pick one without an LLM call (set GOOGLE_API_KEY to enable real resolution).",
             "likely_cause": "ambiguous_duplicate"}


def resolve_unresolved(ledger_pool, gateway_pool, unresolved_ledger_ids, unresolved_gateway_ids):
    """
    Attempts LLM-assisted resolution for each unresolved ledger row against
    unresolved gateway rows from the same merchant.
    Returns (new_matches: list[dict], still_unresolved_ledger, still_unresolved_gateway)
    """
    still_unresolved_gateway = set(unresolved_gateway_ids)
    new_matches = []
    still_unresolved_ledger = []

    for lid in unresolved_ledger_ids:
        lrow = ledger_pool[lid]
        candidates = [
            {**gateway_pool[gid], "settlement_id": gid}
            for gid in still_unresolved_gateway
            if gateway_pool[gid]["merchant"] == lrow["merchant"]
        ]

        result = _call_llm({**lrow, "order_id": lid}, candidates)
        used_llm = result is not None
        if result is None:
            result = _heuristic_fallback(lrow, candidates)

        gid = result.get("match_settlement_id")
        if gid and gid in still_unresolved_gateway:
            new_matches.append({
                "ledger_ids": [lid],
                "gateway_ids": [gid],
                "stage": "llm" if used_llm else "llm_fallback",
                "confidence": result.get("confidence", 0.5),
                "reason": result.get("reason", ""),
            })
            still_unresolved_gateway.discard(gid)
        else:
            cause = result.get("likely_cause") or "other"
            if cause not in CAUSE_LABELS:
                cause = "other"
            still_unresolved_ledger.append({
                "ledger_id": lid,
                "reason": result.get("reason", "No plausible match found."),
                "likely_cause": cause,
                "likely_cause_label": CAUSE_LABELS[cause],
            })

    return new_matches, still_unresolved_ledger, list(still_unresolved_gateway)
