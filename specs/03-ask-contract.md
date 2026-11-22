# Spec 03: Question interface contract

## Commands

| Command | Behaviour |
|---|---|
| `python -m app` | Rebuild the database from the raw CSV, print the data-quality summary, then prompt for questions until a blank line. |
| `python -m app "question"` | Rebuild, answer one question, exit. |
| `python -m app --build` | Rebuild and print the data-quality summary only. Needs no model. |

The rebuild is a full refresh every time (5k rows, well under a second). There is no incremental state
to go stale.

## Answering a question

1. **Validate input.** A question is 3 to 500 characters. Otherwise: error, no model call.
2. **Translate.** Send the model: `specs/02-metrics.md` + the live schema of `invoices` and `accounts`
   + the question. The model has no tools and no file access. It must reply with one JSON object:
   `{"sql": "<one DuckDB SELECT>" | null, "explanation": "<one or two sentences>"}`.
   `sql` is null when the question cannot be answered from this data; `explanation` then says why.
3. **Guard.** The SQL must parse as exactly one statement of type SELECT. Anything else is rejected
   without being executed.
4. **Execute** on a read-only connection with external access disabled (no file or network reads) and
   configuration locked. At most 50 rows are shown.
5. **Retry once.** If the SQL fails the guard or errors in the database, the error is sent back to the
   model for one corrected attempt. A second failure is reported as a failure.
6. **Show** the generated SQL, the result table, the explanation, and the model's latency and cost.

## Output states

| State | Meaning | Exit code (one-shot) |
|---|---|---|
| `answered` | SQL ran; SQL + rows + explanation shown | 0 |
| `refused` | Model returned `sql: null`; the reason is shown | 0 |
| `failed` | Invalid input, model unavailable or timed out, unparseable reply, or SQL failed twice | 1 |

A failure always says which step failed. It never prints a partial or guessed answer.

## Model access

The model is the local Claude Code CLI in headless mode (`claude -p`), with all tools disabled and user
customizations off. The question is passed on stdin, never on the command line. Model name and timeout
come from config (`CLAUDE_MODEL`, `CLAUDE_TIMEOUT`). No API key is stored by this project.

## Out of scope

Multi-turn follow-ups, charts, a prose summary of the result table, an HTTP endpoint, caching.
