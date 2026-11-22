# CloudNova revenue Q&A

CloudNova's finance lead has a messy invoice export and wants to ask revenue questions in plain English
and trust the answers. This repo cleans the export into two tables (`invoices`, `accounts`) with the
business rules built into the columns, then turns a question into one read-only SQL query and shows the
query, the rows and how the number was calculated.

*Take-home for the Forward Deployed Engineer (Data, ML & AI) role, Track B: spec-driven pipeline plus a
natural-language query layer.*

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
| 1 | Recognized revenue for paid invoices in 2024 | **$29,595,624.38** paid (891 invoices). Net of 2024 refunds: **$27,823,962.36** | Good, once the FX decision below is confirmed |
| 2 | Region with the highest average MRR per account | **APAC, $6,147.30** (NA $5,632.84, EMEA $5,327.66, LATAM $5,187.55) | Weak: account-level, and see the MRR warning |
| 3 | Refunds given back | **$8,200,645.87** across 292 invoices | Good, once FX is confirmed |
| 4 | Churn rate, Enterprise vs Starter | **21.92%** (171 of 780) vs **18.81%** (354 of 1,882) | Weak: account-level |
| 5 | Top 5 accounts by revenue, flag CSAT ≤ 2 | ACC-9893 $845,500 (CSAT unknown), **ACC-3142 $764,777.50 (CSAT 2, flagged)**, ACC-1214 $759,750, ACC-6728 $757,400, ACC-5719 $753,405 | Weak: account-level |
| 6 | Invoices stuck in pending/failed, and exposure | **894 invoices, $26,458,551.07** (all-time, not aged) | Good, once FX is confirmed |

## Before using these numbers: two decisions and two warnings

**Decision 1: which way does the FX rate go?** The briefing says "EUR→USD 1.08". But every EUR and GBP
invoice in the file equals the USD list price *multiplied* by the rate, so the export applied the rate
backwards. I convert with `amount / rate`, which makes all 5,000 invoices reconcile to
`list price × seats × months × (1 − discount)` to the dollar. It is one setting (`FX_MODE`), and every
build prints how many invoices match list price (5,000 of 5,000 this way, 3,476 the other).

| | Divide (what I used) | Multiply (literal briefing) |
|---|---|---|
| Q1 paid revenue 2024 | $29,595,624.38 | $32,686,371.20 |
| Q3 refunds | $8,200,645.87 | $8,925,163.84 |
| Q6 exposure | $26,458,551.07 | $28,983,255.97 |
| Q5 top-5 accounts | as listed above | five different accounts |

**Decision 2: who supplies a real account list?** `account_id` is not a reliable key. All 980 accounts
with more than one invoice disagree with themselves on name, region or signup date. I take each
account's attributes from its latest invoice, which is a defensible tie-break, not the truth.
Questions 2, 4 and 5 inherit this. The fix is an account master from the CRM, not more code.

**Warning 1: MRR does not tie to billed revenue.** Current MRR by the briefing's formula is $17.05M a
month across 3,062 active accounts, roughly $205M a year. Billed revenue in 2024 was about $28M. The
export averages 1.3 invoices per account over six years, so it cannot be a complete ledger. Treat MRR
here as "list price × seats for accounts not flagged churned", not as a figure that reconciles to cash.

**Warning 2: undefined terms get a guessed definition.** Asked about "big customers", the model assumed
Enterprise accounts and answered. It said so in the explanation, but that is still a guess. This is the
eval case I expect to fail, and it does.

## Run it

Needs Python 3.10+ (tested on 3.13). Two of the commands need nothing else:

```
pip install -r requirements.txt
python -m app --build        # clean the export, print the data-quality summary
python -m evals --offline    # 45 checks, no model
```

Asking questions also needs the [Claude Code CLI](https://claude.com/claude-code) installed and logged
in (tested with 2.1.290). There are no API keys and nothing secret in `.env`.

```
python -m app                # rebuild, then prompt for questions
python -m app "your question"
python -m evals              # the 45 checks plus 8 live questions
```

Settings (FX mode and rates, model, timeouts, paths) are environment variables with defaults; see
[.env.example](.env.example).

## The spec came first

The commit history shows it: the four specs and the Claude Code config were committed before any code,
and the later fix round changed the specs in their own commit before the code.

| Spec | What it fixes |
|---|---|
| [specs/01-clean-schema.md](specs/01-clean-schema.md) | Category maps, date parsing rule, dedup and quarantine rules, FX, derived columns, the tables. Each judgment call carries its evidence. |
| [specs/02-metrics.md](specs/02-metrics.md) | What revenue, refunds, exposure, MRR and churn mean, and which questions must be refused. **This file is also sent to the model verbatim on every question**, so the glossary people read is the glossary the model uses. |
| [specs/03-ask-contract.md](specs/03-ask-contract.md) | The question interface: input limits, the three output states, guardrails, retry policy. |
| [specs/04-evals.md](specs/04-evals.md) | What is checked, tolerances, and the expected-fail case. |

Diagram: [docs/architecture.md](docs/architecture.md).

## Design decisions and trade-offs

- **Business rules live in table columns, not in the prompt.** `amount_usd`, `recognized_usd` and
  `mrr_usd` are computed once in the pipeline. The model only has to pick and aggregate. Trade-off: a
  rule change means a rebuild, which here takes under a second.
- **The pipeline is plain SQL in DuckDB.** It reads close to the spec, a finance analyst can audit it,
  and it needs one dependency. Trade-off: no incremental loads; every run is a full refresh, in one
  transaction, so a failed build leaves the previous database untouched.
- **Strict parsing, loud failures.** A spelling that is not in the spec, seats of `10.6`, or an amount
  written `1234,50` sends the row to `rejects` with a reason; nothing is guessed or rounded.
- **Duplicates: copies may disagree on status only.** Then the latest lifecycle status wins
  (`refunded > void > paid > failed > pending`); 16 invoices are in that position and the opposite rule
  moves 2024 revenue by about $30K (0.1%). Copies that disagree on anything else, or where one copy was
  rejected, are quarantined rather than booked.
- **Dates are decided per format, from the data.** Slash dates never have a first field above 12 and
  dash dates never have a second field above 12, so slash is month-first and dash is day-first. 87
  invoices remain individually unprovable and are flagged. Day/month swaps cannot change the year, so
  yearly totals are safe and monthly ones carry a caveat, which the model is told to state.
- **The question layer can read two tables and nothing else.** The guard walks DuckDB's own parse tree
  and allows one SELECT over `invoices` and `accounts`. Table functions, `PRAGMA`, system tables,
  `rejects` and `contacts` are refused. It runs read-only with file access off, 1 GB of memory, and a
  10-second limit. Billing-contact emails sit in `contacts`, out of the model's reach.
- **The model is the local Claude Code CLI, headless, with no tools.** No keys to manage or leak.
  Trade-off: about 4 seconds per question, it is not deployable as a service, and it depends on that
  CLI's flags and on whatever the `sonnet` alias points to. `llm()` in [app/ask.py](app/ask.py) is the
  one function to replace.
- **No agent framework.** The question layer is one subprocess call, one guard and one retry.
- **Expected answers are computed, not typed.** [evals/reference.py](evals/reference.py) recomputes
  the six answers from the raw CSV with only the standard library and shares no code with the pipeline.

## Evals

```
python -m evals
PASS 52  FAIL 0  XFAIL 1  XPASS 0
```

- **Offline (45 checks, runs in CI):** 12 invariants on the provided file, 9 reject-path checks on
  synthetic bad rows, 11 comparisons against the independent reference, and 13 guardrail checks (nine
  bypass attempts blocked, the engine sandbox on its own, a normal query still allowed, a second
  connection, and a runaway join stopped by the timeout).
- **Live (8 questions through the real system):** the six stakeholder questions and a "delete all the
  void invoices" request pass. The eighth is expected to fail and does:

  > *"How much revenue came from our big customers?"* should be refused, because "big" is not defined.
  > The system is single-turn and cannot ask, so the model picked a definition (Enterprise plan,
  > $48.9M) and answered.

  The same happened with "What is our ARR?" (it used MRR × 12). Questions the spec lists as
  unanswerable, such as "MRR at the end of 2023", were correctly refused.

## Known limitations and failure modes

- **The FX direction is my inference**, not confirmed by the customer. 10% swing on revenue.
- **Account-level answers rest on an unreliable key**, and MRR does not reconcile to billed revenue.
- **Undefined business terms are guessed, not refused** (the expected-fail case).
- **The reference catches slips, not wrong decisions.** It shares no code with the pipeline but
  implements the same decisions from the same spec. Those decisions are argued in spec 01, not tested.
- **The model is not deterministic.** The live cases passed on all five full runs I made. Five runs is
  not a pass rate; the same question can produce different SQL tomorrow.
- **The live eval checker is lenient.** It passes when the expected value appears in the result rows.
  A result with the right number next to a wrong one would pass. Explanations and the CSAT flag in
  question 5 are not checked by code.
- **Answers do not repeat the FX caveat.** It is in this README and in the build summary, not attached
  to each number.
- **Month and quarter numbers can shift** for the 87 invoices with ambiguous dates. Years cannot.
- **103 invoices are dated in the future** (up to July 2027). They are kept and counted in their year.
- **Single-turn only.** No follow-ups, no clarifying questions, no memory of the last answer.
- **No access control.** Anyone who can run the tool can ask about every account. The 50-row cap limits
  what is displayed, not what a query can aggregate into one cell.
- **The question layer has only been run on Windows**, where the model timeout stops waiting for the
  CLI but may not kill its whole process tree. CI runs the offline layer on Linux.
- **The reject paths are proven on eight synthetic bad rows**, not on a genuinely different export.
  `raw_line` assumes no blank lines and no line breaks inside quoted fields.
- **Fixed FX rates, full refresh only.**

## With another day

1. Take the FX and account-key questions to the customer; both change headline numbers.
2. Add a `clarify` output state and refuse any business term missing from spec 02.
3. Run each live case several times and report a pass rate; add injection and paraphrase cases.
4. Tighten the checker: match label and value in the same row, not just "contains the value".
5. Print a plain-English answer line first, with a "safe to quote / check first" note carrying the FX
   and account caveats, and the SQL last.
6. Let an answer drill down to the invoice ids and raw CSV lines behind it (`raw_line` is already kept).
7. Put a thin `POST /ask` in front, and swap the CLI call for an API or Bedrock call so it can be hosted.
8. Incremental loads, dated FX rates, per-user access to accounts.

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

**Review round.** After the first version was published I had it brief four reviewer subagents, each
with a persona and no knowledge of how the thing was built: a hiring manager grading against the
rubric, CloudNova's finance lead, a senior data engineer and an LLM security engineer. They scored it
6.5 to 7.5 out of 10 and confirmed the six answers independently. They also found real defects, which
were reproduced before being accepted: silent merging of contradictory duplicate rows, `1234,50` read
as 123,450, system functions passing the SQL guard, no query time limit, contact emails reachable by
the model, and a README that did not bridge paid and net revenue or explain MRR. Those are fixed in
the commits from "Specs: stricter parsing" onward, and each code defect now has a check in the offline
evals. What I chose not to fix is in the limitations list above.

## Time

Roughly 15 minutes of planning (profiling and decisions) and 15 minutes of build for the first
version (commits from 22:26 to 22:41). The review round added about 10 minutes of fixes (commits from
23:11 to 23:18) on top of the reviewers' own run time. Everything not done is in the list above.
