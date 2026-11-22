# Spec 02: Metric definitions

This file is the single source of truth for business meaning. It is used twice: the pipeline and evals
are built from it, and `app/ask.py` sends it verbatim to the model as the glossary for every question.

## Tables

- `invoices`: one row per invoice, already cleaned and de-duplicated.
- `accounts`: one row per account, attributes taken from the account's latest invoice.

All money questions are answered in USD using `amount_usd`, `recognized_usd` and `mrr_usd`. Never use
the local-currency `amount` column for totals.

## Revenue

- **Revenue / recognized revenue / net revenue** (default meaning of "revenue"):
  `SUM(recognized_usd)` from `invoices`. This counts `paid` invoices and nets `refunded` ones
  (which are negative). `pending`, `failed` and `void` are never revenue.
- **Paid revenue** (the question says "paid invoices" or "gross"):
  `SUM(amount_usd)` where `status = 'paid'`. Refunds are not netted.
- **Refunds / given back**: `-SUM(amount_usd)` where `status = 'refunded'`, reported as a positive
  number, with the count of refunded invoices.
- **Exposure / stuck / at risk**: invoices with `status IN ('pending', 'failed')`: the count and
  `SUM(amount_usd)`.
- Time is always `invoice_date`. "In 2024" means `year(invoice_date) = 2024`.
- State in the explanation which revenue definition was used.

## MRR

- MRR lives on `accounts.mrr_usd`. It is `list price × seats × (1 − discount)` from the account's
  latest invoice, and 0 for churned accounts.
- **Total MRR**: `SUM(mrr_usd)` over `accounts` where `is_active`.
- **Average MRR per account**: `AVG(mrr_usd)` over `accounts` where `is_active`.
- Never compute MRR from invoice amounts, and never sum `invoices.mrr_usd` across invoices: an annual
  invoice is a 10-month lump sum and one account can have many invoices.
- MRR is a current snapshot only. There is no subscription history in this data.

## Churn

- **Churn rate** = churned accounts ÷ all accounts, from `accounts` (`churned` is boolean).
  Report it as a percentage with the numerator and denominator.
- "Enterprise accounts" means `accounts.plan = 'enterprise'` (the account's current plan).

## Accounts

- **Top accounts by revenue**: `SUM(recognized_usd)` per `account_id`, joined to `accounts` for the
  name and CSAT.
- **Low CSAT**: `csat_score <= 2`. NULL means unknown, not low.

## Values

- `plan`: `starter`, `pro`, `enterprise`
- `status`: `paid`, `pending`, `failed`, `refunded`, `void`
- `billing_cycle`: `monthly`, `annual`
- `region`: `NA`, `EMEA`, `APAC`, `LATAM`
- `currency`: `USD`, `EUR`, `GBP`

## Caveats to surface

- For month or quarter breakdowns, say that some dates are ambiguous
  (`invoice_date_ambiguous`), which can move amounts between months but not between years.
- For account-level answers, say that account attributes come from the latest invoice.

## Questions this data cannot answer

Refuse rather than approximate:

- MRR, ARR or customer counts at a past point in time, MRR growth, net revenue retention, cohort
  retention (no subscription start/end history).
- When an account churned, or churn in a given period (only a current flag exists).
- Forecasts, targets, costs, margins, or anything about data that is not in the two tables.
- Anything that would change data.
