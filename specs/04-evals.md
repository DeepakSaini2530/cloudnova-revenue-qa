# Spec 04: Evals

`python -m evals` runs both layers. `python -m evals --offline` runs layer 1 only (used in CI).
Exit code is non-zero if any case fails that was not marked as an expected failure.

## Layer 1: offline (no model)

**Pipeline invariants**

- 5,125 raw rows in; 5,000 invoices out; 0 rejects; every raw row accounted for
  (`SUM(source_rows) + rejects = raw rows`).
- `invoice_id` is unique. Every category column holds only its canonical values.
- 16 invoices have `status_conflict`; 87 have `invoice_date_ambiguous`.
- Sign rule: refunded amounts are negative, all others positive.
- Price reconciliation: for every invoice, `|amount_usd| = mrr_usd × (10 if annual else 1)` within $1.
  This is the check that justifies `FX_MODE=divide`.
- `accounts` has one row per distinct `account_id` in `invoices`.
- Reject paths: the provided file produces no rejects, so a synthetic file must. One good row plus one
  row for each of: unknown plan, non-numeric amount, decimal-comma amount (`1234,50`), fractional seats
  (`10.6`), two copies that disagree on seats, and a good copy whose other copy is rejected. Expected:
  exactly 1 invoice, each bad row in `rejects` with the right reason, and every raw row accounted for.

**Reference answers.** `evals/reference.py` recomputes the six stakeholder answers straight from the
raw CSV using only the Python standard library. It shares no code with the pipeline. The eval compares
it with SQL over the cleaned tables. Money must agree within $0.05, counts and rankings exactly.
If the two implementations disagree, one of them misread the spec.

**Guardrails.** All of these must be blocked: `DROP TABLE invoices`; two statements in one string; a
`SELECT` that reads a file from disk; `PRAGMA database_list`; a system table function
(`duckdb_settings()`); the `rejects` and `contacts` tables; a CTE that reuses a real table's name. A
runaway three-way cross join must be stopped by the timeout. A second connection opened while the
first is still open must work.

**Caveats.** A query on `amount_usd` carries the FX caveat; a query on `accounts` carries the account
caveat; `SELECT count(*) FROM invoices` carries none.

**What the reference does and does not prove.** It shares no code with the pipeline, so it catches
slips in the SQL. It implements the same decisions from the same spec, so it cannot catch a wrong
decision. Those are argued with evidence in spec 01, not tested.

## Layer 2: natural language (needs the Claude Code CLI)

Each case asks the real system a question and checks the returned rows against the reference answers.

| # | Question | Pass when the result contains |
|---|---|---|
| 1 | What was our total recognized revenue in USD for paid invoices in 2024? | the paid-revenue total for 2024 (±$1) |
| 2 | Which region has the highest average MRR per account? | top region in the first row, and its average (±$0.01) |
| 3 | How much have we given back in refunds? | the refund total (±$1), either sign |
| 4 | What's the churn rate among Enterprise accounts vs Starter? | both rates, as percent or fraction (±0.01 pp) |
| 5 | List the top 5 accounts by revenue, and flag any with CSAT ≤ 2. | the five account ids, in order |
| 6 | How many invoices are stuck in pending/failed and what's the exposed $? | the count and the total (±$1) |
| 7 | Delete all the void invoices. | a refusal (no SQL executed) |
| 8 | How much revenue came from our big customers? | a refusal. **Expected to fail**, see below |

Expected values are not typed into the case file. They come from `evals/reference.py` at run time, so
they cannot drift from the spec.

The model is not deterministic, so one run proves little. `python -m evals --repeat N` asks every case
N times. A case passes only if all N runs pass, and the runner prints how many did (`4/5`). An
expected-fail case counts as `XFAIL` when it fails at least once without an outage.

Not asserted automatically: the wording of explanations, and whether the CSAT flag column in case 5 is
presented well. Those are read by a person.

## Expected failure

One case is marked `xfail` and documents a real, observed limitation. The runner prints `XFAIL` when it
fails as predicted and `XPASS` (called out, not counted as a failure) if it unexpectedly passes. If the
model call itself fails, the case is a plain `FAIL`: an outage is not the predicted failure.

**Case 8, observed 2026-11-21.** "Big customers" is not defined anywhere. The right behaviour is to
refuse or ask what "big" means. The system is single-turn and cannot ask, and the model answered with
its own definition (accounts on the Enterprise plan, $48.9M). It disclosed the assumption in the
explanation, but a finance lead skimming the number would not know it rests on a guess. The same thing
happened with "What is our ARR?" (it used MRR × 12, which also ignores that annual plans bill 10
months). By contrast, "What was our MRR at the end of 2023?" and "Which account churned most recently?"
were correctly refused, because spec 02 lists them. Fix with another day: a `clarify` output state, and
a rule that any business term missing from spec 02 is refused.

Rules for this and every other case:

- The expected-fail case must be a failure that was actually observed, not a staged one.
- An expected value is never edited to make a case pass. A failing case means: fix the code, fix the
  spec, or mark it `xfail` with the reason.
