# CloudNova revenue Q&A

Take-home for the Forward Deployed Engineer (Data, ML & AI) role. Track B: a spec-driven cleaning
pipeline plus a plain-English question layer that shows the SQL behind every answer.

CloudNova's finance lead has a messy invoice export and wants to ask revenue questions in plain English
and trust the answers. This repo cleans the export into two tables (`invoices`, `accounts`) with the
business rules built into the columns, then turns a question into one read-only SQL query and shows the
query, the rows and how the number was calculated.

```
> python -m app "What was our total recognized revenue in USD for paid invoices in 2024?"

SQL:
SELECT ROUND(SUM(amount_usd), 2) AS paid_revenue_usd, COUNT(*) AS paid_invoice_count
FROM invoices WHERE status = 'paid' AND year(invoice_date) = 2024

  paid_revenue_usd  paid_invoice_count
  ----------------  ------------------
  29,595,624.38     891

How: Because the question restricts to paid invoices, I used the paid revenue definition: the sum of
amount_usd for invoices with status 'paid' and an invoice_date in 2024 [...] gross of refunds.

[model: 4.4s, $0.0178]
```

## The six stakeholder questions

| # | Question | Answer | How far to trust it |
|---|---|---|---|
| 1 | Recognized revenue for paid invoices in 2024 | **$29,595,624.38** (891 invoices) | Good, subject to the FX note below |
| 2 | Region with the highest average MRR per account | **APAC, $6,147.30** (NA $5,632.84, EMEA $5,327.66, LATAM $5,187.55) | Weak: account-level |
| 3 | Refunds given back | **$8,200,645.87** across 292 invoices | Good, subject to the FX note |
| 4 | Churn rate, Enterprise vs Starter | **21.92%** (171 of 780) vs **18.81%** (354 of 1,882) | Weak: account-level |
| 5 | Top 5 accounts by revenue, flag CSAT ≤ 2 | ACC-9893 $845,500 (CSAT unknown), **ACC-3142 $764,777.50 (CSAT 2, flagged)**, ACC-1214 $759,750, ACC-6728 $757,400, ACC-5719 $753,405 | Weak: account-level |
| 6 | Invoices stuck in pending/failed, and exposure | **894 invoices, $26,458,551.07** | Good, subject to the FX note |

## Three things to know before trusting these numbers

1. **The FX rule in the briefing looks reversed, and I overrode it.** The briefing says "EUR→USD 1.08".
   But every EUR and GBP invoice in the file equals the USD list price *multiplied* by the rate, so the
   export applied the rate backwards. I convert with `amount / rate`, which makes all 5,000 invoices
   reconcile to `list price × seats × months × (1 − discount)` to the dollar. The literal reading gives
   **$32,686,371.20** for question 1 instead of $29,595,624.38, a 10% gap, and a completely different
   top-5 list. It is one setting (`FX_MODE`) and the first thing I would confirm with the customer.
2. **`account_id` is not a reliable key.** 980 accounts have more than one invoice, and every one of
   them disagrees with itself on name, region or signup date. I take each account's attributes from its
   latest invoice, which is a defensible tie-break, not the truth. Questions 2, 4 and 5 inherit this.
   The fix is an account master from the CRM, not more code.
3. **Undefined terms get a guessed definition.** Asked about "big customers", the model assumed
   Enterprise accounts and answered. It said so in the explanation, but that is still a guess. This is
   the eval case I expect to fail, and it does.

## Run it

Needs Python 3.10+ and, for asking questions, the [Claude Code CLI](https://claude.com/claude-code)
installed and logged in. There are no API keys and nothing secret in `.env`.

```
pip install -r requirements.txt
python -m app
```

That one command rebuilds the database from the raw CSV, prints a data-quality summary, and opens a
prompt for questions. Other entry points:

| Command | What it does |
|---|---|
| `python -m app "your question"` | Answer one question and exit |
| `python -m app --build` | Rebuild and print the data-quality summary (no model needed) |
| `python -m evals --offline` | 26 checks, no model needed. This is what CI runs |
| `python -m evals` | The same plus 8 live questions |

Settings (FX mode and rates, model, paths) are environment variables with defaults; see
[.env.example](.env.example).

## The spec came first

The commit history shows it: the four specs and the Claude Code config were committed before any code.

| Spec | What it fixes |
|---|---|
| [specs/01-clean-schema.md](specs/01-clean-schema.md) | Category maps, date parsing rule, dedup rule, FX, derived columns, the three tables. Each judgment call carries its evidence. |
| [specs/02-metrics.md](specs/02-metrics.md) | What revenue, refunds, exposure, MRR and churn mean, and which questions must be refused. **This file is also sent to the model verbatim on every question**, so the glossary people read is the glossary the model uses. |
| [specs/03-ask-contract.md](specs/03-ask-contract.md) | The question interface: input limits, the three output states, guardrails, retry policy. |
| [specs/04-evals.md](specs/04-evals.md) | What is checked, tolerances, and the expected-fail case. |

Diagram: [docs/architecture.md](docs/architecture.md).

## Design decisions and trade-offs

- **Business rules live in table columns, not in the prompt.** `amount_usd`, `recognized_usd` and
  `mrr_usd` are computed once in the pipeline. The model only has to pick and aggregate. Trade-off: a
  rule change means a rebuild, which here takes under a second.
- **The pipeline is plain SQL in DuckDB.** It reads close to the spec, a finance analyst can audit it,
  and it needs one dependency. Trade-off: no incremental loads; every run is a full refresh.
- **Strict maps, loud failures.** A plan or status spelling that is not in the spec sends the row to
  `rejects` with a reason; it is never guessed. If copies of an invoice ever disagree on amount, the
  build stops. Trade-off: new dirty variants need a spec edit before they load.
- **Dedup: latest lifecycle status wins** (`refunded > void > paid > failed > pending`). Only 16
  invoices truly conflict; the opposite rule moves 2024 revenue by about $30K (0.1%).
- **Dates are decided per format, from the data.** Slash dates never have a first field above 12 and
  dash dates never have a second field above 12, so slash is month-first and dash is day-first. 87
  invoices remain individually unprovable and are flagged. Day/month swaps cannot change the year, so
  yearly totals are safe and monthly ones carry a caveat, which the model is told to state.
- **The model is the local Claude Code CLI, headless, with no tools.** No keys to manage or leak, and
  reviewers who use Claude Code can run it as is. Trade-off: about 4 seconds per question, and it
  is not deployable as a service. `llm()` in [app/ask.py](app/ask.py) is the one function to replace.
- **No agent framework.** The whole question layer is one subprocess call, one guard and one retry.
  There was nothing for LangChain or similar to do that would be worth the extra surface to audit.
- **Expected answers are computed, not typed.** [evals/reference.py](evals/reference.py) recomputes
  the six answers from the raw CSV with only the standard library and shares no code with the pipeline.

## Evals

```
python -m evals
PASS 33  FAIL 0  XFAIL 1  XPASS 0
```

- **Offline (26 checks, runs in CI):** 12 pipeline invariants (row accounting, uniqueness, canonical
  values, sign rule, every amount reconciling to list price, the reject path), 11 comparisons of SQL
  on the cleaned tables against the independent reference, and 3 guardrail checks (`DROP TABLE`, two
  statements, and a `SELECT` that reads a file from disk are all blocked).
- **Live (8 questions through the real system):** the six stakeholder questions and a "delete all the
  void invoices" request pass. The eighth is expected to fail and does:

  > *"How much revenue came from our big customers?"* should be refused, because "big" is not defined.
  > The system is single-turn and cannot ask, so the model picked a definition (Enterprise plan,
  > $48.9M) and answered.

  The same happened with "What is our ARR?" (it used MRR × 12). Questions the spec lists as
  unanswerable, such as "MRR at the end of 2023", were correctly refused.

## Known limitations and failure modes

- **The FX direction is my inference**, not confirmed by the customer. 10% swing on revenue.
- **Account-level answers rest on an unreliable key** (see above).
- **Undefined business terms are guessed, not refused** (the expected-fail case).
- **The model is not deterministic.** The live cases passed on both full runs I made. Two runs is not
  a pass rate; the same question can produce different SQL tomorrow.
- **The live eval checker is lenient.** It passes when the expected value appears in the result rows.
  A result with the right number next to a wrong one would pass. Explanations and the CSAT flag in
  question 5 are not checked by code.
- **Month and quarter numbers can shift** for the 87 invoices with ambiguous dates. Years cannot.
- **103 invoices are dated in the future** (up to July 2027). They are kept and counted in their year.
- **Single-turn only.** No follow-ups, no clarifying questions, no memory of the last answer.
- **The question layer has only been run on Windows.** CI runs the offline layer on Linux.
- **The reject path is proven on two synthetic bad rows**, not on a genuinely different export.
- **Fixed FX rates, full refresh only, 50-row display cap.**

## With another day

1. Take the FX and account-key questions to the customer; both change headline numbers.
2. Add a `clarify` output state and refuse any business term missing from spec 02.
3. Run each live case several times and report a pass rate; add adversarial and paraphrased questions.
4. Tighten the checker: compare the whole result shape, not just "contains the value".
5. Let an answer drill down to the invoice ids and raw CSV lines behind it (`raw_line` is already kept).
6. Put a thin `POST /ask` in front, and swap the CLI call for an API or Bedrock call so it can be hosted.
7. Incremental loads and dated FX rates once there is more than one export.

## How I used AI

Built with Claude Code. The working rules it ran under are in [CLAUDE.md](CLAUDE.md) and
[.claude/settings.json](.claude/settings.json); a trimmed log of the session, including my prompts and
where I overrode it, is in [docs/session-log.md](docs/session-log.md).

In short: I started it in plan mode, where it can only read. It profiled the data before writing
anything, which is what surfaced the reversed FX and the broken account key. It put the decisions that
were mine in front of me; I chose the track, the FX rule and the model transport (I rejected both API
options it offered in favour of the local CLI). The specs were written and committed first, and the
code was generated from them. Where it was wrong: its first spec quoted counts of raw rows where it
meant invoices. The first pipeline run exposed that, and the spec was corrected in its own commit
before the pipeline was committed.

## Time

About 15 minutes of planning (profiling and decisions) and about 25 minutes of build, per the commit
timestamps. I stopped there; everything not done is in the list above.
