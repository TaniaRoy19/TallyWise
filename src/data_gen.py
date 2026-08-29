"""
Generates two synthetic financial data sources that need reconciliation:
  1. internal_ledger.csv   -> the merchant's own order/payment records
  2. gateway_settlement.csv -> Razorpay-style settlement report

Discrepancies are injected on purpose so the reconciliation engine has
real work to do:
  - reference ID typos / case differences
  - date drift (settlement lags order by 1-3 days)
  - amount mismatches (partial refunds, gateway fee deduction)
  - split settlements (one order settled in two rows)
  - duplicate rows
  - orphan rows (present in only one source)
"""

import csv
import os
import random
import string
from datetime import datetime, timedelta

random.seed(42)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
os.makedirs(DATA_DIR, exist_ok=True)

N_RECORDS = 60  # >50 per track requirement

MERCHANTS = ["Acme Retail", "Fablume", "UrbanCart", "Nimbus Foods", "Zestly"]


def rand_ref(n=12):
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=n))


def typo(ref):
    """introduce a small typo to simulate real-world data entry drift"""
    if len(ref) < 4:
        return ref
    i = random.randint(0, len(ref) - 2)
    chars = list(ref)
    chars[i], chars[i + 1] = chars[i + 1], chars[i]
    return "".join(chars)


def main():
    base_date = datetime(2026, 8, 1)
    ledger_rows = []
    gateway_rows = []

    for i in range(N_RECORDS):
        order_id = f"ORD{1000 + i}"
        ref = rand_ref()
        amount = round(random.uniform(199, 24999), 2)
        order_date = base_date + timedelta(days=random.randint(0, 20))
        merchant = random.choice(MERCHANTS)

        # decide what kind of case this record becomes
        case = random.choices(
            ["clean", "typo_ref", "date_drift", "fee_deducted",
             "split_settlement", "duplicate", "ledger_only", "gateway_only"],
            weights=[38, 12, 12, 12, 8, 6, 6, 6],
        )[0]

        ledger_rows.append({
            "order_id": order_id,
            "reference": ref,
            "amount": amount,
            "date": order_date.strftime("%Y-%m-%d"),
            "merchant": merchant,
        })

        if case == "ledger_only":
            continue  # no matching settlement row at all -> real exception

        if case == "clean":
            gateway_rows.append({
                "settlement_id": f"STL{2000+i}",
                "reference": ref,
                "amount": amount,
                "date": order_date.strftime("%Y-%m-%d"),
                "merchant": merchant,
            })

        elif case == "typo_ref":
            gateway_rows.append({
                "settlement_id": f"STL{2000+i}",
                "reference": typo(ref),
                "amount": amount,
                "date": order_date.strftime("%Y-%m-%d"),
                "merchant": merchant,
            })

        elif case == "date_drift":
            drift = timedelta(days=random.randint(1, 3))
            gateway_rows.append({
                "settlement_id": f"STL{2000+i}",
                "reference": ref,
                "amount": amount,
                "date": (order_date + drift).strftime("%Y-%m-%d"),
                "merchant": merchant,
            })

        elif case == "fee_deducted":
            fee = round(amount * random.uniform(0.015, 0.025), 2)
            gateway_rows.append({
                "settlement_id": f"STL{2000+i}",
                "reference": ref,
                "amount": round(amount - fee, 2),
                "date": (order_date + timedelta(days=1)).strftime("%Y-%m-%d"),
                "merchant": merchant,
            })

        elif case == "split_settlement":
            part1 = round(amount * 0.6, 2)
            part2 = round(amount - part1, 2)
            gateway_rows.append({
                "settlement_id": f"STL{2000+i}A",
                "reference": ref,
                "amount": part1,
                "date": (order_date + timedelta(days=1)).strftime("%Y-%m-%d"),
                "merchant": merchant,
            })
            gateway_rows.append({
                "settlement_id": f"STL{2000+i}B",
                "reference": ref,
                "amount": part2,
                "date": (order_date + timedelta(days=2)).strftime("%Y-%m-%d"),
                "merchant": merchant,
            })

        elif case == "duplicate":
            gateway_rows.append({
                "settlement_id": f"STL{2000+i}",
                "reference": ref,
                "amount": amount,
                "date": order_date.strftime("%Y-%m-%d"),
                "merchant": merchant,
            })
            gateway_rows.append({
                "settlement_id": f"STL{2000+i}-dup",
                "reference": ref,
                "amount": amount,
                "date": order_date.strftime("%Y-%m-%d"),
                "merchant": merchant,
            })

    # a few gateway-only rows (settlement with no matching ledger order,
    # e.g. a manual adjustment or a lost record)
    for j in range(4):
        gateway_rows.append({
            "settlement_id": f"STL{9000+j}",
            "reference": rand_ref(),
            "amount": round(random.uniform(199, 5000), 2),
            "date": (base_date + timedelta(days=random.randint(0, 20))).strftime("%Y-%m-%d"),
            "merchant": random.choice(MERCHANTS),
        })

    random.shuffle(ledger_rows)
    random.shuffle(gateway_rows)

    with open(os.path.join(DATA_DIR, "internal_ledger.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["order_id", "reference", "amount", "date", "merchant"])
        w.writeheader()
        w.writerows(ledger_rows)

    with open(os.path.join(DATA_DIR, "gateway_settlement.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["settlement_id", "reference", "amount", "date", "merchant"])
        w.writeheader()
        w.writerows(gateway_rows)

    print(f"Generated {len(ledger_rows)} ledger rows, {len(gateway_rows)} gateway rows.")


if __name__ == "__main__":
    main()