# Session log (trimmed)

Two Claude Code sessions (desktop app), 2026-11-21 and 22: the build with Claude Fable 5.2 (sections 1
to 5), then an independent review and polish pass with Claude Opus 5.5 (section 6). My prompts are
quoted verbatim, trimmed where marked `[...]`; the assistant's turns are summarized. Local paths and
account details are removed. At the end of the second session I added the summary below and trimmed
commentary and minor detail (such as which GitHub account to publish under). No fact about who made a
design or build decision was removed.

## How I steered it

- **Ground rules before any code.** My global Claude Code config tells it to prefer the simplest
  solution that works, standard library and native features first. I started both sessions in plan
  mode, so it had to read and profile the data and propose a plan before it could write anything. I
  approved that plan, including the working rules in `CLAUDE.md`: the specs are the source of truth,
  an expected value is never edited to make an eval pass, and the customer's data is never touched.
- **The calls were mine.** Track B, FX divide, and the model transport, where I overrode its
  recommendation (the Anthropic API) in favour of the local Claude Code CLI. My reasons are in the
  README under "My calls and why".
- **Proof, not claims.** I had it run every demo command as I would on camera, then had it review its
  own work through four reviewer personas using subagents, and told it to fix what they found.
- **A second opinion from a different model before sending.** Claude Opus 5.5 reviewed the repo cold
  against the brief. I approved the changes it proposed and the ones it declined, and it asked before
  pushing.

## 1. Kickoff (plan mode, read-only)

> **Me:** I have received email from OpsGuru - Hello Deepak, Please find the Technical Assessment
> attached. Instructions for the assessment are included in the document. There are 1 main file and 2
> supporting files. After finishing the assignment, please share the github link with the solution.
> Please reach out if you have any questions. 3 Attached documents are in "[my Downloads folder]"

I started the session in plan mode, so the assistant could read but not write.

**Assistant:** Read the assignment, `BUSINESS_CONTEXT.md` and the head of the CSV. Then profiled the
whole CSV with two throwaway standard-library Python scripts (nothing written to disk). Findings it
reported:

- 5,125 rows, 5,000 invoice ids; 82 exact copies; 43 ids whose copies differ, only in `status`; 16
  real status conflicts after normalization.
- Every EUR/GBP amount equals USD list price **times** the briefing's rate, so the stated "EUR→USD
  1.08" is applied backwards in the data. It computed the 2024 revenue both ways ($29.6M vs $32.7M).
- Slash dates are always month-first and dash dates always day-first (zero counter-examples), which
  resolves the "ambiguous" format at the family level.
- `account_id` is effectively not a key: almost every multi-invoice account has conflicting names.
- Candidate answers to all six stakeholder questions, under two dedup rules and both FX directions, to
  show which decisions actually move the numbers (FX: 10%; dedup rule: 0.1%).

## 2. The decisions it put to me

| Question | Options it offered | My answer |
|---|---|---|
| Track | B: pipeline + NL-to-SQL (its recommendation) · A: RAG | **Track B** |
| Model provider | Anthropic API (its recommendation) · Gemini | > local claude code |
| FX conversion default | divide by the rate (its recommendation) · multiply (literal briefing) | **Divide by the rate** |

**Override:** I rejected both model options and told it to use the local Claude Code CLI instead. A
consequence is that the project holds no API key at all. The assistant then verified the CLI's real
flags with `claude --help` instead of assuming them, and designed the call with tools disabled and the
question passed on stdin.

## 3. Plan review

The assistant wrote a plan: findings table, spec decisions, file layout, build order with specs
committed first, verification steps, and the actions it would ask permission for (publishing).

> **Me:** Shall we create a new project folder under "[my personal projects folder]" ?

**Redirect:** it had proposed building inside an existing folder of mine; I asked for a new standalone
project folder instead. The assistant agreed and noted the side benefit that nothing unrelated can end
up in a public repo. Plan updated, then approved.

## 4. Build (about 15 minutes, see commit timestamps)

Steps 4 to 7 were written and run together and committed as separate logical commits once the evals
were green, which is why those commits share a timestamp.

1. Scaffold, repo-local git identity, raw data copied unchanged. *Commit.*
2. Four specs, `CLAUDE.md`, `.claude/settings.json`. *Commit, before any code.*
3. Smoke test of `claude -p --safe-mode --tools "" --output-format json --system-prompt-file ...`:
   worked first time, 5 seconds.
4. `app/pipeline.py` and `evals/reference.py` written from the spec, then run against each other.
   They agreed on all six answers on the first run.
5. **Correction:** the run showed the spec's counts were raw rows, not invoices (88 vs 87 ambiguous
   dates, 1,566 vs 1,524 non-USD, 1,049 vs 980 multi-invoice accounts). The spec was fixed in its own
   commit before the pipeline commit. No rule changed.
6. `app/ask.py`, the CLI and the eval runner. First live question returned $29,595,624.38 in 4.4s.
   All seven live cases passed on the first full run.
7. **Finding the expected-fail case.** The assistant's first candidate, "What was our MRR at the end of
   2023?", was correctly refused, so it was not a failure. It then probed six harder questions. Two
   were real failures of the same kind: "big customers" and "What is our ARR?" were answered with
   invented definitions. "Big customers" became the expected-fail case; both are in the README.
8. Added one check for the reject path, because the provided file never triggers it.
9. README, architecture diagram, this log, CI workflow.

## 5. Publish, test, review round

The assistant stopped before publishing and asked. I approved, it pushed, and CI passed on GitHub.
It then wrote a talk track for a walkthrough recording.

> **Me:** run them and test

It ran every command in the talk track in PowerShell, as I would on camera: both live questions, the
offline evals and the full suite. All passed.

> **Me:** Now use sub agents and rate our application using different personas

It briefed four reviewer subagents in parallel. Each got a persona, the repo and the assignment brief,
was told to review only, and was not told how the thing was built or what the assistant thought its
weak points were. Each used its own copy of the database so they could run things concurrently.

| Persona | Score | Main finding |
|---|---|---|
| Hiring manager, grading the rubric | 7.5 | Would advance. Weakest rubric item: my own judgment is barely visible in this log |
| Senior data engineer | 7 | All six answers reproduced independently. Four ways a slightly different export would be booked wrongly |
| CloudNova finance lead | 6.5 | Paid and net revenue never bridged; MRR does not tie to billed revenue; README written for engineers |
| LLM security engineer | 6.5 | System functions passed the guard as "SELECT"; no query time limit; emails reachable by the model |

The assistant did not take the reports on trust. It reproduced each defect itself before listing it,
and found one more while doing so (a second database connection failed while the first was open). It
then gave me three groups: things only I could do, things it could fix, and things to leave as
documented limits.

> **Me:** Yes, fix them

Specs were changed and committed first, then the pipeline, the guard and the evals. The offline layer
grew from 26 to 45 checks. Left as documented limits, by choice: the lenient live checker, the model
timeout not killing the CLI's process tree on Windows, and no per-user access control.

## 6. Independent review by a second model, then a polish pass

A new Claude Code session with a different model (Claude Opus 5.5), started in plan mode, with no
summary of how the repo was built beyond what is in the repo itself.

> **Me:** Review the project developed by Fable for a usecase provided by OpsGuru for recruitment. I
> choose one of the usecase from the 3 files shared by OpsGuru. [...]

**Assistant:** Read the brief and both supporting files, then the whole repo. It checked the main
claims itself rather than taking the README's word for them:

- the files in `data/` are byte-identical to the ones OpsGuru sent;
- dividing by the FX rate reconciles 5,000 of 5,000 invoices to list price, multiplying 3,476
  (recomputed with the standard library, outside the pipeline);
- duplicate copies differ only in `status`;
- the database matches every number in the README;
- ten more guard bypass attempts (`getenv`, `information_schema`, `SUMMARIZE`, FROM-first syntax, a
  file read through a replacement scan, `PIVOT` over `contacts`, a recursive CTE) were all blocked or
  stopped by the timeout;
- commits carry no personal email and the CI runs are green.

Verdict: no wrong answer. Main risk: rubric item 2, because my own reasoning was not visible. Small
findings, none of which changes an answer:

1. 6 of the 87 dates flagged as ambiguous have the same day and month, so swapping changes nothing.
2. The guard refuses the legitimate schema-qualified name `main.invoices` (costs one retry).
3. Every decimal printed to 2 places, so a churn rate returned as 0.2192 would show as 0.22.
4. A genuine $0 invoice (100% discount) would be rejected with a misleading reason.
5. `python -m app --build extra` is read as a question.
6. `evals/reference.py` strips every non-digit from amounts, so it is only valid for this file.

My first answer was **review only, no changes**. Then:

> **Me:** I need to build and send this for my job application. This is the first round. [...]

**Assistant:** Proposed sharpening what the rubric grades rather than adding features, closing
weaknesses the README already admitted. Before writing anything about my reasoning it asked me why I
made my three main calls, offering candidate reasons. I picked the ones that were mine and left the
rest out: "no key to leak", "the data beats the wording", "keep it switchable, then ask" and "totals
need SQL" were offered and not picked. My picks are the README's "My calls and why", unchanged in
substance.

Changed, spec first (see `git log`):

- Every answer now prints "Check first" caveats (FX direction, MRR vs billed revenue, account
  attributes) for the columns its SQL touched, taken from column comments, so a number cannot travel
  without its warning even if the model leaves it out of the explanation.
- `python -m evals --repeat 5` gives a real pass rate in place of "it passed on five runs".
- Finding 3 fixed. Finding 1 written into the limitations.
- README: a bottom line for the finance lead at the top, "My calls and why", and CI named as the one
  bonus hardening touch.

Declined, with reasons:

- A `clarify` state to fix the expected-fail case. The brief asks for one real, observed failure;
  fixing it would mean staging a new one.
- A Dockerfile or `POST /ask`. The brief says pick one hardening touch.
- Findings 2, 4, 5 and 6. They fail safe, and fixing them would change spec counts without changing
  any answer.

## What I would point a reviewer at

- The spec-first commit order is real: `git log --reverse`.
- `specs/02-metrics.md` is both documentation and the model's runtime glossary.
- The "Check first" caveats come from column comments (`app/pipeline.py`), not from the model.
- The assistant was not allowed to fix a failing eval by editing the expected value (`CLAUDE.md`,
  rule 3). Expected values are computed by `evals/reference.py`, so there was nothing to edit anyway.
- The one place I disagreed with the briefing (FX) is a config switch, with the evidence in spec 01.
