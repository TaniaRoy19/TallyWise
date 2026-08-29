"""
Core reconciliation engine.

Stage 1 - Exact match:      same reference string, same amount, same date.
Stage 2 - Fuzzy match:      close reference (edit distance), amount within
                            2.5% (fee deduction) or exact, date within 3 days.
                            Also detects split settlements (N gateway rows
                            summing to one ledger amount).
Stage 3 - LLM-assisted:     whatever remains unresolved goes to the LLM
                            resolver (see llm_resolver.py) with full context,
                            which either proposes a match + confidence + a
                            plain-language reason, or says it can't resolve it.

Everything is scored and logged so the final report can state exactly which
stage resolved each pair, not just a final yes/no.
"""

from dataclasses import dataclass, field
from datetime import datetime
from itertools import combinations


def edit_distance(a, b):
    if a == b:
        return 0
    dp = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        dp[i][0] = i
    for j in range(len(b) + 1):
        dp[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + cost)
    return dp[-1][-1]


def days_between(d1, d2):
    return abs((datetime.strptime(d1, "%Y-%m-%d") - datetime.strptime(d2, "%Y-%m-%d")).days)


@dataclass
class MatchResult:
    ledger_ids: list
    gateway_ids: list
    stage: str            # exact | fuzzy_reference | fuzzy_fee | split | llm | unresolved
    confidence: float
    reason: str


@dataclass
class ReconciliationReport:
    matches: list = field(default_factory=list)
    unresolved_ledger: list = field(default_factory=list)
    unresolved_gateway: list = field(default_factory=list)


def reconcile(ledger, gateway):
    """
    ledger, gateway: list[dict] loaded from the CSVs.
    Returns a ReconciliationReport.
    """
    ledger_pool = {row["order_id"]: row for row in ledger}
    gateway_pool = {row["settlement_id"]: row for row in gateway}

    matched_ledger_ids = set()
    matched_gateway_ids = set()
    matches = []

    # --- Stage 1: exact match ---
    for lid, lrow in ledger_pool.items():
        if lid in matched_ledger_ids:
            continue
        for gid, grow in gateway_pool.items():
            if gid in matched_gateway_ids:
                continue
            if (lrow["reference"] == grow["reference"]
                    and abs(float(lrow["amount"]) - float(grow["amount"])) < 0.01
                    and lrow["date"] == grow["date"]):
                matches.append(MatchResult([lid], [gid], "exact", 1.0,
                                            "Reference, amount and date all identical."))
                matched_ledger_ids.add(lid)
                matched_gateway_ids.add(gid)
                break

    # --- Stage 2a: fuzzy reference / date drift / fee deduction ---
    for lid, lrow in ledger_pool.items():
        if lid in matched_ledger_ids:
            continue
        best = None
        for gid, grow in gateway_pool.items():
            if gid in matched_gateway_ids:
                continue
            if lrow["merchant"] != grow["merchant"]:
                continue
            ref_dist = edit_distance(lrow["reference"], grow["reference"])
            date_gap = days_between(lrow["date"], grow["date"])
            amt_l, amt_g = float(lrow["amount"]), float(grow["amount"])
            amt_diff_pct = abs(amt_l - amt_g) / amt_l if amt_l else 1

            if ref_dist <= 2 and date_gap <= 3 and amt_diff_pct < 0.001:
                score = 0.9 - 0.05 * ref_dist - 0.02 * date_gap
                cand = (score, gid, "fuzzy_reference",
                        f"Reference differs by {ref_dist} character swap(s) "
                        f"(likely data-entry drift); amount exact; settled "
                        f"{date_gap} day(s) after order.")
            elif ref_dist == 0 and 0.01 < amt_diff_pct < 0.03 and date_gap <= 3:
                score = 0.85
                cand = (score, gid, "fuzzy_fee",
                        f"Same reference; amount short by "
                        f"{amt_diff_pct*100:.2f}% (consistent with gateway "
                        f"fee deduction); settled {date_gap} day(s) later.")
            else:
                continue

            if best is None or cand[0] > best[0]:
                best = cand

        if best:
            score, gid, stage, reason = best
            matches.append(MatchResult([lid], [gid], stage, round(score, 2), reason))
            matched_ledger_ids.add(lid)
            matched_gateway_ids.add(gid)

    # --- Stage 2b: split settlements (2 gateway rows summing to 1 ledger row) ---
    remaining_gateway = [gid for gid in gateway_pool if gid not in matched_gateway_ids]
    for lid, lrow in ledger_pool.items():
        if lid in matched_ledger_ids:
            continue
        same_ref_candidates = [
            gid for gid in remaining_gateway
            if edit_distance(lrow["reference"], gateway_pool[gid]["reference"]) <= 1
            and lrow["merchant"] == gateway_pool[gid]["merchant"]
        ]
        if len(same_ref_candidates) < 2:
            continue
        found = False
        for pair in combinations(same_ref_candidates, 2):
            total = sum(float(gateway_pool[g]["amount"]) for g in pair)
            if abs(total - float(lrow["amount"])) < 0.01:
                matches.append(MatchResult(
                    [lid], list(pair), "split", 0.88,
                    f"Two settlement rows ({pair[0]}, {pair[1]}) sum to "
                    f"exactly the ledger amount \u2014 likely a split settlement."
                ))
                matched_ledger_ids.add(lid)
                matched_gateway_ids.update(pair)
                remaining_gateway = [g for g in remaining_gateway if g not in pair]
                found = True
                break
        if found:
            continue

    unresolved_ledger = [lid for lid in ledger_pool if lid not in matched_ledger_ids]
    unresolved_gateway = [gid for gid in gateway_pool if gid not in matched_gateway_ids]

    return ReconciliationReport(matches, unresolved_ledger, unresolved_gateway), ledger_pool, gateway_pool