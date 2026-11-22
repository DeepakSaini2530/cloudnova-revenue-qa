# Spec 01: Cleaned schema

Status: approved before any code was written. The pipeline (`app/pipeline.py`) is generated from this
file. If behaviour needs to change, change this file first.

## Input

`data/cloudnova_invoices.csv`: UTF-8, header + 5,125 rows, 19 columns. Every column is read as text.
`raw_line` is the line number in the file (header = 1), kept for lineage.

## Row-level cleaning

All text is trimmed. Category matching is case-insensitive. **Maps are strict: a value that is not
listed sends the row to `rejects` with a reason. Nothing is guessed or passed through.**

| Column | Rule |
|---|---|
| `plan` | `starter`, `start`, `tier 1` → `starter` · `pro`, `professional`, `tier 2` → `pro` · `enterprise`, `ent`, `tier 3` → `enterprise` |
| `billing_cycle` | `monthly` → `monthly` · `annual`, `annually`, `yearly` → `annual` |
| `status` | `paid`, `complete`, `completed`, `success` → `paid` · `pending`, `in_review`, `awaiting` → `pending` · `failed`, `declined`, `error` → `failed` · `refunded`, `refund`, `charged_back` → `refunded` · `void`, `cancelled`, `canceled` → `void` |
| `payment_method` | `ach` → `ach` · `credit_card`, `credit card` → `credit_card` · `wire`, `wire transfer` → `wire` · `invoice` → `invoice` · blank → NULL |
| `churned` | `true`, `yes`, `1`, `y` → true · `false`, `no`, `0`, `n` → false |
| `currency` | must be `USD`, `EUR` or `GBP` |
| `region` | must be `NA`, `EMEA`, `APAC` or `LATAM` |
| `amount` | Remove `$`, `€`, `£`, thousands commas, spaces and a trailing `USD`/`EUR`/`GBP`. The rest must be a number with at most 2 decimals. If the text carried a currency symbol or code, it must agree with `currency`. Stored as `amount` DECIMAL(14,2) in the invoice currency. |
| `seats` | integer > 0 |
| `discount_pct` | number, 0 to 100 |
| `csat_score` | integer 1 to 5, blank → NULL |
| `support_tickets` | integer ≥ 0 |
| `contact_email` | lower-cased, `_at_` → `@`, blank → NULL. No further validation. |
| `industry` | blank → NULL |
| `signup_date`, `invoice_date` | see Dates |

### Dates

Five shapes exist. They do not overlap, so each value matches exactly one.

| Shape | Read as | Example |
|---|---|---|
| `YYYY-MM-DD` | ISO | `2024-01-05` |
| `YYYY/MM/DD` | ISO with slashes | `2024/01/05` |
| `MM/DD/YYYY` | month first | `01/05/2024` |
| `DD-MM-YYYY` | day first | `05-01-2024` |
| `Mon D YYYY` | month name | `Jan 5 2024` |

Why slash = month-first and dash = day-first: in 2,579 slash dates the first field is never above 12,
and in 532 dash dates the second field is never above 12. The data settles it per shape, with zero
counter-examples. Dash dates where both fields are ≤ 12 cannot be proven individually, so those rows
get `invoice_date_ambiguous = true` (88 invoices). Swapping day and month never changes the year, so
**yearly totals are unaffected; monthly and quarterly totals are not.**

## Duplicates: one row per `invoice_id`

Observed: 5,125 rows hold 5,000 invoice ids. 82 rows are exact copies. 43 ids have copies that differ,
and they differ **only in `status`**. After normalization 27 of those agree; 16 are real conflicts
(paid/pending 7, paid/void 5, paid/refunded 2, paid/failed 2).

Rule: after row-level cleaning, keep one row per `invoice_id`. When statuses disagree, the latest
lifecycle state wins:

`refunded` > `void` > `paid` > `failed` > `pending`

Reasoning: pending and failed are in-flight states that a later system can overwrite with paid; void
and refunded are reversals that can only happen after the fact. The invoice gets
`status_conflict = true`, and `source_rows` records how many raw rows were merged.

If copies ever differ in `amount`, the build fails. No rule has been agreed for that case and it
does not occur in this file.

Sensitivity: the opposite rule (the non-revenue status always wins) changes 2024 paid revenue by about
$30K, or 0.1%.

## Sign rule (after dedup)

`refunded` invoices must have a negative amount; every other status must be positive. A violation goes
to `rejects`. (The two negative "paid" rows in the raw file are both paid/refunded conflicts, which the
dedup rule resolves to `refunded`.)

## Currency: `amount_usd`

Rates are fixed: EUR 1.08, GBP 1.27 (config `FX_EUR`, `FX_GBP`).

**Default `FX_MODE=divide`: `amount_usd = round(amount / rate, 2)`.**

This departs from the literal wording of the briefing ("EUR→USD 1.08"), on evidence: all 1,566 EUR/GBP
invoices equal `list price × seats × months × (1 − discount) × rate` to within a dollar. The export
multiplied USD by the rate to get the local amount, so dividing is what recovers USD and keeps revenue
consistent with the MRR formula. `FX_MODE=multiply` gives the literal reading. The gap is material
(about 10% on 2024 revenue), so the README reports both and it is the first question for the customer.

## Derived columns

- `mrr_usd` = `list_price[plan] × seats × (1 − discount_pct/100)`, list prices Starter 49, Pro 99,
  Enterprise 299. Billing cycle does not matter: an annual invoice maps to the same monthly figure.
- `recognized_usd` = `amount_usd` when status is `paid` or `refunded`, else 0. Refunds are negative, so
  summing this column nets them out.

## Tables

### `invoices` (one row per invoice)

`invoice_id` (unique), `account_id`, `account_name`, `contact_email`, `region`, `industry`, `plan`,
`billing_cycle`, `seats`, `currency`, `amount`, `amount_usd`, `discount_pct`, `status`,
`payment_method`, `signup_date`, `invoice_date`, `churned`, `csat_score`, `support_tickets`,
`mrr_usd`, `recognized_usd`, `status_conflict`, `invoice_date_ambiguous`, `source_rows`, `raw_line`.

### `accounts` (one row per `account_id`)

Attributes come from the account's **latest invoice** (highest `invoice_date`, ties broken by highest
`invoice_id`), whatever its status: `account_name`, `contact_email`, `region`, `industry`, `plan`,
`billing_cycle`, `seats`, `discount_pct`, `currency`, `signup_date`, `churned`, `csat_score`,
`support_tickets`.

Plus: `is_active` (= not churned), `mrr_usd` (latest invoice's `mrr_usd`, or 0 when churned),
`invoice_count`, `latest_invoice_id`, `latest_invoice_date`, and `has_conflicting_attributes` (true
when the account's invoices disagree on `account_name`, `region` or `signup_date`).

**Known weakness:** `account_id` is not a reliable key in this export. Of the 1,049 accounts with more
than one invoice, 976 (93%) show different account names across invoices. "Latest invoice wins" is a
defensible tie-break, not the truth. Account-level answers are the least trustworthy numbers this
system produces until the customer supplies an account master.

### `rejects`

`raw_line`, `invoice_id`, `reason`. Expected to be empty for the provided file. A row is never
silently dropped: it is either in `invoices` (possibly merged with its copies) or in `rejects`.

## Not handled (deliberately)

- Future-dated invoices are kept as-is (104 are dated after 2026-11-21, up to 2027).
- `signup_date` ambiguity is not flagged; no metric uses it.
- No email validation beyond the `_at_` repair.
