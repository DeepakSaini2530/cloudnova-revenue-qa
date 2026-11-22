# CloudNova revenue Q&A

Clean a dirty invoice export into DuckDB, then answer plain-English revenue questions by generating SQL
and showing it. Built for a finance lead who needs to trust the numbers.

## How to work in this repo

1. **The specs are the source of truth.** `specs/01` to `04` define behaviour. To change behaviour,
   edit the spec first, then the code, in that order and in separate commits where practical.
   If code and spec disagree, the code is wrong.
2. **Business meaning lives in `specs/02-metrics.md` and in table columns, not in prompts or Python.**
   That file is sent to the model verbatim at run time. Do not add a second glossary anywhere.
3. **Never edit an expected value to make an eval pass.** A failing eval means fix the code, fix the
   spec, or mark the case `xfail` with a reason. Expected answers come from `evals/reference.py`,
   which must stay independent of `app/` (standard library only, no imports from `app`).
4. **Never modify anything in `data/`.** It is the customer's export.
5. **Simplest thing that works.** Standard library and DuckDB before new code. No new dependencies
   without asking. No abstractions with one implementation. No speculative features: put them in the
   README's next-steps list instead.
6. **Fail loudly.** A row is never silently dropped or rounded into shape (it goes to `rejects` with a
   reason). A question should be refused or reported as failed rather than answered with a guess.
   Known gap: business terms missing from spec 02 are still guessed (eval case 8). Do not hide it.
7. **Be honest in docs.** Limits, failed cases and untested paths are written down as such.

## Commands

- `python -m app --build`: rebuild the database and print the data-quality summary
- `python -m app "question"`: answer one question
- `python -m evals --offline`: invariants + reference answers + guardrails (no model needed)
- `python -m evals`: everything, including the natural-language cases

Run `python -m evals --offline` before every commit that touches `app/` or `evals/`.

## Layout

- `app/config.py`: all settings, read from the environment with defaults (`.env.example`)
- `app/pipeline.py`: raw CSV to `invoices`, `accounts`, `rejects` (SQL, full refresh)
- `app/ask.py`: question to SQL through the local `claude` CLI, guardrails, execution
- `evals/`: runner, cases, independent reference
- `docs/`: architecture diagram, trimmed session log

## Things that are easy to get wrong here

- FX: `amount_usd = amount / rate` by default. See spec 01 for why. Do not "fix" it to multiply.
- Annual invoices bill 10 months, not 12.
- MRR comes from `accounts.mrr_usd`, never from summing invoices.
- User questions go to the `claude` CLI on stdin only. Never interpolate them into a command line.
- The question layer may read `invoices` and `accounts` only. Contact emails live in `contacts` so the
  model cannot reach them. Do not add personal data to the two readable tables.
- DuckDB classes `PRAGMA` and table functions as SELECT. Guard on the parse tree, not the statement type.
- Answer caveats live in column and table comments (`Caveat: ...` in `app/pipeline.py`). `ask.py` only
  surfaces them for the names the SQL references. Do not write caveat text into `ask.py` or the prompt.
- Never read or commit `.env`.
