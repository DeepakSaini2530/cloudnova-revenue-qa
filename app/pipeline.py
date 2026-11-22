"""Raw invoice CSV -> cleaned DuckDB tables. Implements specs/01-clean-schema.md.

Full refresh on every run: 5k rows rebuild in well under a second, so there is no
incremental state to go stale.
"""
import duckdb

from . import config

# Strict maps (spec 01). A value that is not listed sends the row to `rejects`.
MAPS = {
    "plan": {
        "starter": "starter", "start": "starter", "tier 1": "starter",
        "pro": "pro", "professional": "pro", "tier 2": "pro",
        "enterprise": "enterprise", "ent": "enterprise", "tier 3": "enterprise",
    },
    "billing_cycle": {"monthly": "monthly", "annual": "annual", "annually": "annual", "yearly": "annual"},
    "status": {
        "paid": "paid", "complete": "paid", "completed": "paid", "success": "paid",
        "pending": "pending", "in_review": "pending", "awaiting": "pending",
        "failed": "failed", "declined": "failed", "error": "failed",
        "refunded": "refunded", "refund": "refunded", "charged_back": "refunded",
        "void": "void", "cancelled": "void", "canceled": "void",
    },
    "payment_method": {
        "ach": "ach", "credit_card": "credit_card", "credit card": "credit_card",
        "wire": "wire", "wire transfer": "wire", "invoice": "invoice",
    },
    "churned": {
        "true": "true", "yes": "true", "1": "true", "y": "true",
        "false": "false", "no": "false", "0": "false", "n": "false",
    },
}
# When copies of an invoice disagree on status, the earliest entry in this list wins.
STATUS_PRECEDENCE = ["refunded", "void", "paid", "failed", "pending"]

# Sent to the model with the schema, so the meaning travels with the column.
COMMENTS = {
    "invoices": {
        "amount": "Invoice amount in the invoice currency. Do not sum across currencies.",
        "amount_usd": "Invoice amount in USD. Negative for refunded invoices.",
        "recognized_usd": "amount_usd if status is paid or refunded, else 0. SUM = revenue net of refunds.",
        "mrr_usd": "Monthly recurring revenue implied by this invoice's plan, seats and discount.",
        "status": "paid | pending | failed | refunded | void",
        "plan": "starter | pro | enterprise",
        "billing_cycle": "monthly | annual (an annual invoice bills 10 months)",
        "invoice_date": "Issue date. Use this for all time filters.",
        "status_conflict": "True when source systems disagreed on status for this invoice.",
        "invoice_date_ambiguous": "True when day and month could be swapped. Never changes the year.",
        "source_rows": "Number of raw rows merged into this invoice.",
        "raw_line": "Line number of the surviving row in the raw CSV.",
        "churned": "Account churn flag as it appeared on this invoice. Prefer accounts.churned.",
    },
    "accounts": {
        "mrr_usd": "Current MRR in USD from the latest invoice. 0 for churned accounts.",
        "is_active": "True when the account has not churned.",
        "churned": "True when the account has churned.",
        "plan": "Current plan: starter | pro | enterprise",
        "csat_score": "Latest CSAT 1 to 5. NULL means unknown.",
        "has_conflicting_attributes": "True when this account's invoices disagree on name, region or signup date.",
        "invoice_count": "Number of invoices for this account.",
    },
}

SETUP_SQL = r"""
CREATE OR REPLACE TEMP MACRO clean(v) AS nullif(trim(v), '');
CREATE OR REPLACE TEMP MACRO canon(col_name, v) AS
    (SELECT m.canon FROM maps m WHERE m.col = col_name AND m.raw = lower(trim(v)));
CREATE OR REPLACE TEMP MACRO parse_date(v) AS (CASE
    WHEN regexp_full_match(trim(v), '\d{4}-\d{2}-\d{2}') THEN try_strptime(trim(v), '%Y-%m-%d')
    WHEN regexp_full_match(trim(v), '\d{4}/\d{2}/\d{2}') THEN try_strptime(trim(v), '%Y/%m/%d')
    WHEN regexp_full_match(trim(v), '\d{2}/\d{2}/\d{4}') THEN try_strptime(trim(v), '%m/%d/%Y')
    WHEN regexp_full_match(trim(v), '\d{2}-\d{2}-\d{4}') THEN try_strptime(trim(v), '%d-%m-%Y')
    WHEN regexp_full_match(trim(v), '[A-Za-z]{3} \d{1,2} \d{4}') THEN try_strptime(trim(v), '%b %d %Y')
END)::DATE;
CREATE OR REPLACE TEMP MACRO to_int(v) AS
    (CASE WHEN regexp_full_match(clean(v), '\d+') THEN try_cast(clean(v) AS INTEGER) END);
CREATE OR REPLACE TEMP MACRO amount_text(v) AS regexp_replace(trim(v), '[$€£\s]|(USD|EUR|GBP)$', '', 'g');
CREATE OR REPLACE TEMP MACRO amount_ccy(v) AS (CASE
    WHEN contains(v, '€') OR trim(v) LIKE '%EUR' THEN 'EUR'
    WHEN contains(v, '£') OR trim(v) LIKE '%GBP' THEN 'GBP'
    WHEN contains(v, '$') OR trim(v) LIKE '%USD' THEN 'USD'
END);
"""

# Row-level cleaning. `row_reason` is NULL for a good row, else the list of failed checks.
PARSE_SQL = r"""
CREATE OR REPLACE TEMP TABLE cleaned AS
WITH c AS (
    SELECT
        raw_line,
        clean(invoice_id) AS invoice_id,
        clean(account_id) AS account_id,
        clean(account_name) AS account_name,
        replace(lower(clean(contact_email)), '_at_', '@') AS contact_email,
        upper(clean(region)) AS region,
        clean(industry) AS industry,
        canon('plan', plan) AS plan,
        canon('billing_cycle', billing_cycle) AS billing_cycle,
        canon('status', status) AS status,
        canon('payment_method', payment_method) AS payment_method,
        clean(payment_method) AS payment_method_raw,
        canon('churned', churned) = 'true' AS churned,
        to_int(seats) AS seats,
        upper(clean(currency)) AS currency,
        -- commas only as thousands separators: '1234,50' is rejected, not read as 123450
        CASE WHEN regexp_full_match(amount_text(amount), '-?(\d{1,3}(,\d{3})+|\d+)(\.\d{1,2})?')
             THEN replace(amount_text(amount), ',', '')::DECIMAL(14,2) END AS amount,
        amount_ccy(amount) AS amount_ccy,
        CASE WHEN regexp_full_match(clean(discount_pct), '\d+(\.\d{1,2})?')
             THEN try_cast(clean(discount_pct) AS DECIMAL(5,2)) END AS discount_pct,
        to_int(csat_score) AS csat_score,
        clean(csat_score) AS csat_raw,
        to_int(support_tickets) AS support_tickets,
        parse_date(signup_date) AS signup_date,
        parse_date(invoice_date) AS invoice_date,
        coalesce(regexp_full_match(trim(invoice_date), '(0[1-9]|1[0-2])-(0[1-9]|1[0-2])-\d{4}'), false)
            AS invoice_date_ambiguous
    FROM raw
)
SELECT *, nullif(concat_ws(', ',
    CASE WHEN invoice_id IS NULL THEN 'missing invoice_id' END,
    CASE WHEN account_id IS NULL THEN 'missing account_id' END,
    CASE WHEN plan IS NULL THEN 'unknown plan' END,
    CASE WHEN billing_cycle IS NULL THEN 'unknown billing_cycle' END,
    CASE WHEN status IS NULL THEN 'unknown status' END,
    CASE WHEN payment_method IS NULL AND payment_method_raw IS NOT NULL THEN 'unknown payment_method' END,
    CASE WHEN churned IS NULL THEN 'unknown churned' END,
    CASE WHEN currency IS NULL OR currency NOT IN ('USD', 'EUR', 'GBP') THEN 'unknown currency' END,
    CASE WHEN region IS NULL OR region NOT IN ('NA', 'EMEA', 'APAC', 'LATAM') THEN 'unknown region' END,
    CASE WHEN amount IS NULL THEN 'unparseable amount' END,
    CASE WHEN amount_ccy <> currency THEN 'amount currency disagrees with currency column' END,
    CASE WHEN seats IS NULL OR seats <= 0 THEN 'bad seats' END,
    CASE WHEN discount_pct IS NULL OR discount_pct NOT BETWEEN 0 AND 100 THEN 'bad discount_pct' END,
    CASE WHEN csat_raw IS NOT NULL AND (csat_score IS NULL OR csat_score NOT BETWEEN 1 AND 5)
         THEN 'bad csat_score' END,
    CASE WHEN support_tickets IS NULL OR support_tickets < 0 THEN 'bad support_tickets' END,
    CASE WHEN signup_date IS NULL THEN 'unparseable signup_date' END,
    CASE WHEN invoice_date IS NULL THEN 'unparseable invoice_date' END
), '') AS row_reason
FROM c;
"""

# Copies of an invoice may disagree on status only. Anything else has no agreed rule, so every copy
# is quarantined: a good row is not booked while one of its copies is rejected or contradicts it.
QUARANTINE_SQL = """
CREATE OR REPLACE TEMP TABLE parsed AS
SELECT * EXCLUDE (row_reason, fingerprint),
    coalesce(row_reason, CASE
        WHEN bool_or(row_reason IS NOT NULL) OVER w THEN 'another copy of this invoice was rejected'
        WHEN min(fingerprint) OVER w <> max(fingerprint) OVER w
            THEN 'copies of this invoice disagree on more than status'
    END) AS reason
FROM (
    SELECT *, row(account_id, account_name, contact_email, region, industry, plan, billing_cycle,
                  payment_method, churned, seats, currency, amount, discount_pct, csat_score,
                  support_tickets, signup_date, invoice_date)::VARCHAR AS fingerprint
    FROM cleaned
)
WINDOW w AS (PARTITION BY invoice_id);
"""

# One row per invoice_id; latest lifecycle status wins; then the sign rule.
DEDUP_SQL = """
CREATE OR REPLACE TEMP TABLE deduped AS
SELECT * EXCLUDE (rk),
       (status = 'refunded') <> (amount < 0) OR amount = 0 AS sign_mismatch
FROM (
    SELECT *,
        count(*) OVER (PARTITION BY invoice_id) AS source_rows,
        min(status) OVER (PARTITION BY invoice_id) <> max(status) OVER (PARTITION BY invoice_id)
            AS status_conflict,
        row_number() OVER (
            PARTITION BY invoice_id
            ORDER BY list_position({precedence}, status), raw_line
        ) AS rk
    FROM parsed
    WHERE reason IS NULL
)
WHERE rk = 1;
"""

TABLES_SQL = """
CREATE OR REPLACE TABLE rejects AS
SELECT raw_line, invoice_id, reason FROM parsed WHERE reason IS NOT NULL
UNION ALL
SELECT p.raw_line, p.invoice_id, 'amount sign does not match status'
FROM parsed p JOIN deduped d USING (invoice_id)
WHERE p.reason IS NULL AND d.sign_mismatch
ORDER BY raw_line;

-- Personal data, kept apart: the question layer is only allowed to read invoices and accounts.
CREATE OR REPLACE TABLE contacts AS
SELECT invoice_id, account_id, contact_email FROM deduped WHERE NOT sign_mismatch ORDER BY invoice_id;

CREATE OR REPLACE TABLE invoices AS
SELECT
    d.invoice_id, d.account_id, d.account_name, d.region, d.industry,
    d.plan, d.billing_cycle, d.seats, d.currency, d.amount,
    round(d.amount {fx_op} fx.rate, 2)::DECIMAL(14,2) AS amount_usd,
    d.discount_pct, d.status, d.payment_method, d.signup_date, d.invoice_date,
    d.churned, d.csat_score, d.support_tickets,
    round(price.usd * d.seats * (1 - d.discount_pct / 100), 2)::DECIMAL(14,2) AS mrr_usd,
    CASE WHEN d.status IN ('paid', 'refunded') THEN amount_usd ELSE 0 END::DECIMAL(14,2) AS recognized_usd,
    d.status_conflict, d.invoice_date_ambiguous, d.source_rows, d.raw_line
FROM deduped d
JOIN fx ON fx.currency = d.currency
JOIN price ON price.plan = d.plan
WHERE NOT d.sign_mismatch
ORDER BY d.invoice_id;

CREATE OR REPLACE TABLE accounts AS
WITH ranked AS (
    SELECT *, row_number() OVER (
        PARTITION BY account_id ORDER BY invoice_date DESC, invoice_id DESC) AS rk
    FROM invoices
), agg AS (
    SELECT account_id, count(*) AS invoice_count,
           count(DISTINCT account_name) > 1 OR count(DISTINCT region) > 1
               OR count(DISTINCT signup_date) > 1 AS has_conflicting_attributes
    FROM invoices GROUP BY account_id
)
SELECT
    r.account_id, r.account_name, r.region, r.industry, r.plan, r.billing_cycle,
    r.seats, r.discount_pct, r.currency, r.signup_date, r.churned, NOT r.churned AS is_active,
    r.csat_score, r.support_tickets,
    CASE WHEN r.churned THEN 0 ELSE r.mrr_usd END::DECIMAL(14,2) AS mrr_usd,
    a.invoice_count, r.invoice_id AS latest_invoice_id, r.invoice_date AS latest_invoice_date,
    a.has_conflicting_attributes
FROM ranked r JOIN agg a USING (account_id)
WHERE r.rk = 1
ORDER BY r.account_id;
"""

SUMMARY_SQL = """
SELECT
    (SELECT count(*) FROM raw) AS raw_rows,
    (SELECT count(*) FROM invoices) AS invoices,
    (SELECT count(*) FROM accounts) AS accounts,
    (SELECT count(*) FROM rejects) AS rejects,
    (SELECT coalesce(sum(source_rows - 1), 0) FROM invoices) AS duplicate_rows_merged,
    (SELECT count(*) FROM invoices WHERE status_conflict) AS status_conflicts,
    (SELECT count(*) FROM invoices WHERE invoice_date_ambiguous) AS ambiguous_invoice_dates,
    (SELECT count(*) FROM invoices WHERE currency <> 'USD') AS non_usd_invoices,
    (SELECT count(*) FROM invoices
     WHERE abs(abs(amount_usd) - mrr_usd * CASE WHEN billing_cycle = 'annual' THEN 10 ELSE 1 END) <= 1)
        AS reconciled_to_list_price,
    (SELECT count(*) FROM accounts WHERE has_conflicting_attributes) AS accounts_with_conflicts
"""


def build(raw_csv=None, db_path=None) -> dict:
    """Rebuild invoices, accounts and rejects from the raw CSV. Returns the data-quality summary."""
    raw_csv, db_path = raw_csv or config.RAW_CSV, db_path or config.DB_PATH
    if not raw_csv.exists():
        raise SystemExit(f"Raw CSV not found: {raw_csv} (set RAW_CSV)")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        return _build(raw_csv, db_path)
    except duckdb.Error as e:  # locked file, missing column, wrong encoding, empty file...
        raise SystemExit(f"Build failed; the previous database is unchanged. {type(e).__name__}: {e}") from None


def _build(raw_csv, db_path) -> dict:
    with duckdb.connect(str(db_path)) as con:
        con.execute("BEGIN TRANSACTION")  # all three tables are replaced together or not at all
        con.execute("CREATE OR REPLACE TEMP TABLE maps (col VARCHAR, raw VARCHAR, canon VARCHAR)")
        con.executemany("INSERT INTO maps VALUES (?, ?, ?)",
                        [(col, raw, canon) for col, m in MAPS.items() for raw, canon in m.items()])
        con.execute("CREATE OR REPLACE TEMP TABLE fx (currency VARCHAR, rate DOUBLE)")
        con.executemany("INSERT INTO fx VALUES (?, ?)", list(config.FX_RATES.items()))
        con.execute("CREATE OR REPLACE TEMP TABLE price (plan VARCHAR, usd INTEGER)")
        con.executemany("INSERT INTO price VALUES (?, ?)", list(config.LIST_PRICE.items()))
        con.execute(SETUP_SQL)
        # Single-threaded read keeps file order, so raw_line is the real line number (header = 1).
        con.execute(
            "CREATE OR REPLACE TEMP TABLE raw AS SELECT row_number() OVER () + 1 AS raw_line, * "
            "FROM read_csv(?, all_varchar = true, header = true, parallel = false)",
            [str(raw_csv)],
        )
        con.execute(PARSE_SQL)
        con.execute(QUARANTINE_SQL)
        con.execute(DEDUP_SQL.format(precedence=STATUS_PRECEDENCE))
        con.execute(TABLES_SQL.format(fx_op="/" if config.FX_MODE == "divide" else "*"))
        for table, cols in COMMENTS.items():
            for col, text in cols.items():
                con.execute(f"COMMENT ON COLUMN {table}.{col} IS '{text.replace(chr(39), chr(39) * 2)}'")
        cur = con.execute(SUMMARY_SQL)
        summary = dict(zip([d[0] for d in cur.description], cur.fetchone()))
        summary["reject_reasons"] = con.execute(
            "SELECT reason, count(*) FROM rejects GROUP BY reason ORDER BY 2 DESC, 1").fetchall()
        con.execute("COMMIT")
    summary["fx_mode"] = config.FX_MODE
    return summary


def print_summary(s: dict) -> None:
    print(f"Built {config.DB_PATH.name} from {config.RAW_CSV.name}")
    print(f"  raw rows                 {s['raw_rows']:>6,}")
    print(f"  invoices (one per id)    {s['invoices']:>6,}   ({s['duplicate_rows_merged']} duplicate rows merged)")
    print(f"  accounts                 {s['accounts']:>6,}   ({s['accounts_with_conflicts']} with conflicting attributes)")
    print(f"  rejected rows            {s['rejects']:>6,}")
    for reason, n in s["reject_reasons"]:
        print(f"      {n:>5}  {reason}")
    print(f"  status conflicts         {s['status_conflicts']:>6,}   (latest lifecycle state wins)")
    print(f"  ambiguous invoice dates  {s['ambiguous_invoice_dates']:>6,}   (read as DD-MM-YYYY)")
    print(f"  non-USD invoices         {s['non_usd_invoices']:>6,}   (FX_MODE={s['fx_mode']})")
    print(f"  match list price         {s['reconciled_to_list_price']:>6,}   of {s['invoices']:,} "
          "(a low count means the FX setting is wrong)")
