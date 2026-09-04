# TallyWise

**An AI agent that reconciles your payment records and lets you ask it anything about them, on demand.**

Built for the Razorpay Buildathon (Track 04: AI Finance Controller).

---

## Setup & run
**Offline setup — works immediately, zero setup, no API key required.**

```bash
pip install -r requirements.txt
python src/server.py
```
# On Mac/Linux, use python3 instead of python if "python" isn't found

Open `http://127.0.0.1:5000`, click **"Run Reconciliation"**, and watch it work. Then type a question like *"why wasn't ORD1005 settled?"* into the QA box at the bottom.
It falls back to a deterministic offline mode for AI resolution and Q&A.

**Want real AI-powered answers instead of the offline fallback?** Add a free Gemini key:
```
# create a .env file in the project root with:
GOOGLE_API_KEY=your-gemini-key-here
```
Get one free at **aistudio.google.com** — no billing required. 
We use **gemini-3.1-flash-lite as the primary model**, with **gemini-3.5-flash-lite as an automatic fallback** if the primary is briefly unavailable,
chosen after testing real free-tier quotas directly, since some Gemini model variants have quotas too small to be usable at all.
If both models fail (e.g. a temporary outage), **the system falls back to a deterministic offline heuristic rather than crashing.**


**Prefer the command line instead of the web app?**
```bash
python src/main.py                                     # runs the full pipeline
python src/qa_agent.py "why wasn't ORD1005 settled?"   # ask about a specific order
```

---

## The problem, in one line

Every business keeps two records of the same money — their own order list, and the payment gateway's settlement report — and these two lists never match perfectly. Someone has to manually hunt down every typo, delay, and missing payment. That's what TallyWise automates.

## What it actually does

1. **Match the easy stuff instantly.** Same reference, same amount, same date → done. No AI needed.
2. **Catch the messy-but-predictable stuff with rules.** Typos, late settlements, gateway fees, split payments — all handled by fast, deterministic logic.
3. **Bring in AI only for the genuinely hard cases.** Partial refunds, compound errors, ambiguous duplicates — the ~15-20% of cases that actually need judgment. The AI either finds the match and explains why, or says "I can't confidently resolve this" and tells you exactly what kind of problem it looks like.
4. **Answer questions about any of it, live.** Ask "why wasn't ORD1005 settled?" and get a real answer, grounded in the actual data — not a canned response.

**The result:** a match rate, a clear breakdown of how each match was found, an honest list of what's left over with a reason for every single one — and an agent you can actually talk to about all of it.

## Ask it anything

This is what makes it feel like an agent, not a script. Every unresolved
or resolved order can be queried directly, in plain English, and the
answer is grounded in the real report data — never a guess.

```
> why wasn't ORD1005 settled?

ORD1005 (₹1,774.49, Zestly) has only one same-merchant candidate,
STL2008 at ₹13,834.13 — nearly 8x the order amount, far outside any
reasonable tolerance.
```

Works from the terminal (`qa_agent.py`) or the live web dashboard's
question box — same underlying logic, same grounding, same honesty
about what it doesn't know.

## Why this is harder than it looks

We didn't just make a CSV matcher — we deliberately built in the messy stuff real finance teams actually deal with:

| What's messy        | Example |
|---------------------|---------|
| Typos               | A reference number gets one character swapped |

| Delays              | Settlement arrives 1-3 days after the order |

| Fees                | Gateway takes a small cut before settling |

| Split payments      | One order gets paid out in two separate transfers |

| **Partial refunds** | Customer gets refunded before settlement — the amounts are   meaningfully different, not just off by a rounding error |

| **Compound errors** | A typo AND a delay on the same row, because real bad data rarely breaks one thing at a time |

The last two are the ones that actually need AI — they're not solvable with simple rules, and that's on purpose.

## The results

| | Rules alone | With AI |
|---|---|---|
| **Default run** | 80.0% | **83.3%** |

We didn't stop at one lucky number. We tested this across 4 different random datasets to make sure it holds up:

| Dataset  | Rules alone | With AI     |
|----------|-------------|-------------|
| Seed 42  | 80.0%       | 85.0%       |
| Seed 7   | 83.3%       | 86.7%       |
| Seed 123 | 90.0%       | 90.0%       |
| Seed 2026| 76.7%       | 76.7%       |

| **Average** | **82.5%** | **84.6%** |

**On two of those four runs, the AI resolved zero extra cases** — not because it failed, but because those particular hard cases genuinely had no confident answer, and it correctly said so instead of guessing. Here's a real example of that happening:

> *"No candidate row matches the ledger amount of 9390.15, and the candidate with matching reference IIUA56X5T69T has a different amount (6283.92)."*

The AI noticed a reference number matched — but the amount was off by over ₹3,100, and it refused to call that a match. A system that never says "I don't know" isn't one you can trust with real money.

## What happens to the leftovers

Every unresolved case gets sorted into one of three buckets, so a human knows exactly what to do next:

- 🔴 **Likely missing settlement** — nothing even close exists → escalate to the gateway
- 🟡 **Likely data-entry error** — one close-but-not-exact candidate → a human should double-check this specific field
- 🔵 **Ambiguous / multiple candidates** — a few equally plausible options → needs a manual pick

## How it's built

   Ledger + settlement CSVs
            │
            ▼
   Exact match (Stage 1)
            │  leftovers only
            ▼
   Fuzzy match (Stage 2) ── typos, delays, fees, splits
            │  leftovers only
            ▼
   AI resolution (Stage 3) ── Gemini reasons over a narrow, specific  candidate set  
            │                 (tries a 2nd model as
            |                 backup if the 1st is unavailable)
            |                                                
            ▼                   

   Report: match rate + reasoned breakdown + categorized exceptions
            │
      ┌─────┴─────┐
      ▼           ▼
 Terminal Q&A   Web dashboard + Q&A

**Why rules-first, AI-last?** Cheap, predictable patterns don't need an LLM — that would be slower, more expensive, and harder to audit for no real benefit. AI only touches the narrow slice of cases that genuinely need judgment, and every decision it makes — match or decline — comes with a stated, checkable reason. Nothing is a black box.

**Why does the API key never touch the browser?** The web app calls our own backend, which calls Gemini server-side. The key is never sent to your browser. This is the correct pattern for handling any API key in a web app — worth doing right, especially for something adjacent to payments.

## Project layout

```
src/
  data_gen.py       # generates realistic messy test data (60+ rows)
  reconcile.py       # exact + fuzzy rule matching (stages 1-2)
  llm_resolver.py     # AI resolution + cause classification (stage 3)
  main.py             # runs everything, writes the reports
  qa_agent.py         # terminal Q&A
  server.py           # web app + API
  templates/index.html
```

## What's next

- A review queue that ranks exceptions by how close they came to resolving
- Real Razorpay test-mode settlement data instead of synthetic CSVs
- A manual-audit spot check to add a second layer of verified accuracy
