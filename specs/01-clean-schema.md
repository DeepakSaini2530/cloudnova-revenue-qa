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
| `amount` | Remove `$`, `€`, `£`, spaces and a trailing `USD`/`EUR`/`GBP`. The rest must be `-?(\d{1,3}(,\d{3})+|\d+)(\.\d{1,2})?`: commas are accepted only as thousands separators, so `1,200.00` is 1200.00 and `1234,50` (decimal comma) is rejected, not read as 123,450. If the text carried a currency symbol or code, it must agree with `currency`. Stored as `amount` DECIMAL(14,2) in the invoice currency. |
| `seats` | digits only, > 0. `10.6` or `1e2` is rejected, never rounded. |
| `discount_pct` | number with at most 2 decimals, 0 to 100 |
| `csat_score` | digits only, 1 to 5, blank → NULL |
| `support_tickets` | digits only |
| `contact_email` | lower-cased, `_at_` → `@`, blank → NULL. No further validation. Stored only in `contacts` (see Tables). |
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
get `invoice_date_ambiguous = true` (88 raw rows, 87 invoices). Swapping day and month never changes the year, so
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

Copies may disagree on `status` and nothing else. Two cases have no agreed rule, do not occur in this
file, and are quarantined rather than guessed: every copy of the invoice goes to `rejects`.

- Copies disagree on any other cleaned column (amount, seats, currency, dates, plan, account...).
  Reason: `copies of this invoice disagree on more than status`.
- One copy fails row-level cleaning. The surviving copy is not booked on its own, because the rejected
  copy might have been the one that should win (for example a refund with a misspelt plan).
  Reason: `another copy of this invoice was rejected`.

Sensitivity: the opposite rule (the non-revenue status always wins) changes 2024 paid revenue by about
$30K, or 0.1%.

## Sign rule (after dedup)

`refunded` invoices must have a negative amount; every other status must be positive. A violation
sends every raw row of that invoice to `rejects`. (The two negative "paid" rows in the raw file are both paid/refunded conflicts, which the
dedup rule resolves to `refunded`.)

## Currency: `amount_usd`

Rates are fixed: EUR 1.08, GBP 1.27 (config `FX_EUR`, `FX_GBP`).

**Default `FX_MODE=divide`: `amount_usd = round(amount / rate, 2)`.**

This departs from the literal wording of the briefing ("EUR→USD 1.08"), on evidence: all 1,566 EUR/GBP
rows (1,524 invoices) equal `list price × seats × months × (1 − discount) × rate` to within a dollar,
where months is 10 for annual and 1 for monthly. The export
multiplied USD by the rate to get the local amount, so dividing is what recovers USD and keeps revenue
consistent with the MRR formula. `FX_MODE=multiply` gives the literal reading. The gap is material
(about 10% on 2024 revenue), so the README reports both and it is the first question for the customer.

## Derived columns

- `mrr_usd` = `list_price[plan] × seats × (1 − discount_pct/100)`, list prices Starter 49, Pro 99,
  Enterprise 299. Billing cycle does not matter: an annual invoice maps to the same monthly figure.
- `recognized_usd` = `amount_usd` when status is `paid` or `refunded`, else 0. Refunds are negative, so
  summing this column nets them out.

## Tables

Column and table comments carry the meaning to the model with the schema. A comment may end in
`Caveat: <text>`; the question layer prints that text under any answer that uses the column or table
(spec 03). The caveats are the ones listed in spec 02.

### `invoices` (one row per invoice)

`invoice_id` (unique), `account_id`, `account_name`, `region`, `industry`, `plan`,
`billing_cycle`, `seats`, `currency`, `amount`, `amount_usd`, `discount_pct`, `status`,
`payment_method`, `signup_date`, `invoice_date`, `churned`, `csat_score`, `support_tickets`,
`mrr_usd`, `recognized_usd`, `status_conflict`, `invoice_date_ambiguous`, `source_rows`, `raw_line`.

### `accounts` (one row per `account_id`)

Attributes come from the account's **latest invoice** (highest `invoice_date`, ties broken by highest
`invoice_id`), whatever its status: `account_name`, `region`, `industry`, `plan`,
`billing_cycle`, `seats`, `discount_pct`, `currency`, `signup_date`, `churned`, `csat_score`,
`support_tickets`.

Plus: `is_active` (= not churned), `mrr_usd` (latest invoice's `mrr_usd`, or 0 when churned),
`invoice_count`, `latest_invoice_id`, `latest_invoice_date`, and `has_conflicting_attributes` (true
when the account's invoices disagree on `account_name`, `region` or `signup_date`). In this file that
flag is true for exactly the 980 accounts with more than one invoice, so here it says nothing that
`invoice_count > 1` does not.

**Known weakness:** `account_id` is not a reliable key in this export. Of the 980 accounts with more
than one invoice, 976 show different account names across invoices, and all 980 disagree on name,
region or signup date. "Latest invoice wins" is a
defensible tie-break, not the truth. Account-level answers are the least trustworthy numbers this
system produces until the customer supplies an account master.

### `contacts` (one row per invoice)

`invoice_id`, `account_id`, `contact_email`. Billing-contact emails are personal data and no
stakeholder question needs them, so they are kept out of `invoices` and `accounts`. The question layer
cannot read this table (spec 03).

### `rejects`

`raw_line`, `invoice_id`, `reason`: one row per rejected raw row. Expected to be empty for the provided
file. A row is never silently dropped: every raw row is either merged into an invoice (counted in its
`source_rows`) or listed here, so `SUM(source_rows) + rejects = raw rows` always holds.

`raw_line` counts records, with the header as line 1. It equals the physical line number as long as the
file has no blank lines and no line breaks inside quoted fields, which is true of this file.

## Not handled (deliberately)

- Future-dated invoices are kept as-is (103 are dated after 2026-11-21, the latest 2027-07-23).
- `signup_date` ambiguity is not flagged; no metric uses it.
- No email validation beyond the `_at_` repair.
