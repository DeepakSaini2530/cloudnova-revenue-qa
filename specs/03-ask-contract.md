# Spec 03: Question interface contract

## Commands

| Command | Behaviour |
|---|---|
| `python -m app` | Rebuild the database from the raw CSV, print the data-quality summary, then prompt for questions until a blank line. |
| `python -m app "question"` | Rebuild, answer one question, exit. |
| `python -m app --build` | Rebuild and print the data-quality summary only. Needs no model. |

The rebuild is a full refresh every time (5k rows, well under a second). There is no incremental state
to go stale. It runs in one transaction: a build that fails says so and leaves the previous database
unchanged.

## Answering a question

1. **Validate input.** A question is 3 to 500 characters. Otherwise: error, no model call.
2. **Translate.** Send the model: `specs/02-metrics.md` + the live schema of `invoices` and `accounts`
   + the question. The model has no tools and no file access. It must reply with one JSON object:
   `{"sql": "<one DuckDB SELECT>" | null, "explanation": "<one or two sentences>"}`.
   `sql` is null when the question cannot be answered from this data; `explanation` then says why.
3. **Guard.** The SQL is parsed, not pattern-matched. It must be exactly one SELECT that reads only
   `invoices` and `accounts` (plus its own CTEs). Table functions, `PRAGMA`, `SHOW`/`DESCRIBE`, system
   tables, `rejects` and `contacts` are rejected without being executed. A CTE may not reuse the name of
   a real table.
4. **Execute** on a read-only connection with external access disabled (no file or network reads),
   1 GB of memory, 2 threads and configuration locked. A query still running after `QUERY_TIMEOUT`
   seconds (default 10) is interrupted. At most 50 rows are shown.
5. **Retry once.** If the reply is not the expected JSON, the SQL fails the guard, or the database
   errors or times out, the error is sent back to the model for one corrected attempt. A second failure
   is reported as a failure.
6. **Show** the generated SQL, the result table, the explanation, the caveats, and the model's latency
   and cost.

**Caveats** ("Check first") come from the data, not from the model. The guard's parse tree already
names every table and column the SQL references. For each one that has a comment ending in
`Caveat: <text>` (spec 01), that text is printed under the answer. `ask.py` holds no caveat text.
`SELECT *` names no columns, so it gets the table caveats only.

## Output states

| State | Meaning | Exit code (one-shot) |
|---|---|---|
| `answered` | SQL ran; SQL + rows + explanation + caveats shown | 0 |
| `refused` | Model returned `sql: null`; the reason is shown | 0 |
| `failed` | Invalid input, model unavailable or timed out, database busy, or two bad replies in a row | 1 |

A failure always says which step failed. It never prints a partial or guessed answer.

## Model access

The model is the local Claude Code CLI in headless mode (`claude -p`), with all tools disabled and user
customizations off. The question is passed on stdin, never on the command line. Model name and timeout
come from config (`CLAUDE_MODEL`, `CLAUDE_TIMEOUT`). No API key is stored by this project.

## Known gaps in this contract

- The 50-row cap limits what is displayed, not what a query can aggregate into one cell.
- On Windows the model timeout stops waiting for the CLI but may not kill its whole process tree.
- There is no per-user access control: everyone who can run the tool can ask about every account.

## Out of scope

Multi-turn follow-ups, charts, a prose summary of the result table, an HTTP endpoint, caching.
