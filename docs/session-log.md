# Session log (trimmed)

One Claude Code session (desktop app, model Claude Fable 5.2), 2026-11-21. My prompts are quoted
verbatim; the assistant's turns are summarized. Local paths and account details are removed. Nothing
else is edited to look better than it was.

Standing instruction in my global Claude Code config, active for the whole session: *prefer the
simplest solution that works; standard library and native features before new code or dependencies.*

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

It also checked the environment: installed packages, that no Anthropic API key was set, and that my
global git config would have put an employer email on public commits.

## 2. The four decisions it asked me to make

| Question | Options it offered | My answer |
|---|---|---|
| Track | B: pipeline + NL-to-SQL (its recommendation) · A: RAG | **Track B** |
| Model provider | Anthropic API (its recommendation) · Gemini | > local claude code |
| Git identity for a public repo | account 1 + noreply email · account 2 + noreply email · keep the employer email | account 2, noreply email |
| FX conversion default | divide by the rate (its recommendation) · multiply (literal briefing) | **Divide by the rate** |

**Override:** I rejected both model options. I did not want an API key in the project at all, so the
question layer calls the local Claude Code CLI in headless mode. The assistant then verified the CLI's
real flags with `claude --help` instead of assuming them, and designed the call with tools disabled
and the question passed on stdin.

## 3. Plan review

The assistant wrote a plan: findings table, spec decisions, file layout, build order with specs
committed first, verification steps, and the actions it would ask permission for (publishing).

> **Me:** Shall we create a new project folder under "[my personal projects folder]" ?

**Redirect:** it had proposed building inside an existing folder of mine. I moved it to a standalone
folder so nothing unrelated could end up in a public repo. Plan updated, then approved.

## 4. Build (about 25 minutes, see commit timestamps)

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

## What I would point a reviewer at

- The spec-first commit order is real: `git log --reverse`.
- `specs/02-metrics.md` is both documentation and the model's runtime glossary.
- The assistant was not allowed to fix a failing eval by editing the expected value (`CLAUDE.md`,
  rule 3). Expected values are computed by `evals/reference.py`, so there was nothing to edit anyway.
- The one place I disagreed with the briefing (FX) is a config switch, with the evidence in spec 01.
