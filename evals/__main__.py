"""Eval runner. Implements specs/04-evals.md.

    python -m evals             offline layer + natural-language cases
    python -m evals --offline   offline layer only (no model needed; this is what CI runs)
"""
import json
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

import duckdb

from app import ask, config, pipeline
from evals import reference

CASES = Path(__file__).parent / "cases.json"
tally = {"PASS": 0, "FAIL": 0, "XFAIL": 0, "XPASS": 0}


def record(name: str, ok: bool, detail: str = "", xfail: str | None = None) -> None:
    status = ("XPASS" if ok else "XFAIL") if xfail else ("PASS" if ok else "FAIL")
    tally[status] += 1
    print(f"  {status:5}  {name}" + (f"  [{detail}]" if detail else ""))


def accounted(s: dict) -> bool:
    """Every raw row is either merged into an invoice or listed in rejects."""
    return s["invoices"] + s["duplicate_rows_merged"] + s["rejects"] == s["raw_rows"]


def invariants(con: duckdb.DuckDBPyConnection, summary: dict, ref: dict) -> None:
    def one(sql: str):
        return con.execute(sql).fetchone()[0]

    print("\nPipeline invariants (the provided file)")
    record("5,125 raw rows in", summary["raw_rows"] == 5125, str(summary["raw_rows"]))
    record("5,000 invoices out", summary["invoices"] == 5000 == ref["invoices"], str(summary["invoices"]))
    record("0 rejects", summary["rejects"] == 0, str(summary["rejects"]))
    record("every raw row accounted for", accounted(summary))
    record("invoice_id unique", one("SELECT count(*) - count(DISTINCT invoice_id) FROM invoices") == 0)
    record("canonical category values only", one("""
        SELECT count(*) FROM invoices
        WHERE plan NOT IN ('starter', 'pro', 'enterprise') OR billing_cycle NOT IN ('monthly', 'annual')
           OR status NOT IN ('paid', 'pending', 'failed', 'refunded', 'void')
           OR currency NOT IN ('USD', 'EUR', 'GBP') OR region NOT IN ('NA', 'EMEA', 'APAC', 'LATAM')
           OR payment_method NOT IN ('ach', 'credit_card', 'wire', 'invoice')""") == 0)
    record("16 status conflicts", summary["status_conflicts"] == 16 == ref["status_conflicts"],
           str(summary["status_conflicts"]))
    record("87 ambiguous invoice dates",
           summary["ambiguous_invoice_dates"] == 87 == ref["ambiguous_invoice_dates"],
           str(summary["ambiguous_invoice_dates"]))
    record("sign rule: refunds negative, others positive",
           one("SELECT count(*) FROM invoices WHERE (status = 'refunded') <> (amount < 0) OR amount = 0") == 0)
    record("every amount reconciles to list price (justifies FX divide)",
           summary["reconciled_to_list_price"] == summary["invoices"], str(summary["reconciled_to_list_price"]))
    record("accounts: one row per account_id",
           one("SELECT count(*) FROM accounts") == one("SELECT count(DISTINCT account_id) FROM invoices")
           == ref["accounts"])
    record("contact emails are only in the contacts table", one("""
        SELECT count(*) FROM duckdb_columns()
        WHERE column_name = 'contact_email' AND table_name <> 'contacts'""") == 0)


def reject_paths() -> None:
    """The provided file rejects nothing, so prove each reject path with rows that must fail."""
    print("\nReject paths (synthetic rows)")
    header, good = config.RAW_CSV.read_text(encoding="utf-8").splitlines()[:2]
    assert all(s in good for s in ("INV-104380", "STARTER", "52920.00", ",100,EUR,")), "fixture row changed"

    def row(invoice_id: str, old: str = "", new: str = "") -> str:
        return good.replace("INV-104380", invoice_id).replace(old, new)

    rows = [good,
            row("INV-PLAN", "STARTER", "Platinum"),
            row("INV-AMOUNT", "52920.00", "52920.00x"),
            row("INV-COMMA", "52920.00", '"1234,50"'),
            row("INV-SEATS", ",100,EUR,", ",10.6,EUR,"),
            row("INV-CLASH"), row("INV-CLASH", ",100,EUR,", ",5,EUR,"),
            row("INV-HALF"), row("INV-HALF", "STARTER", "Platinum")]
    expected = {"unknown plan": 2,
                "unparseable amount": 2,  # non-numeric, and a decimal comma
                "bad seats": 1,
                "copies of this invoice disagree on more than status": 2,
                "another copy of this invoice was rejected": 1}
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp, "synthetic.duckdb")
        Path(tmp, "bad.csv").write_text("\n".join([header, *rows]), encoding="utf-8")
        s = pipeline.build(Path(tmp, "bad.csv"), db)
        got = dict(s["reject_reasons"])
        record("only the good row is booked", s["invoices"] == 1, f"{s['invoices']} invoice")
        for reason, n in expected.items():
            record(f"rejected: {reason}", got.get(reason) == n, f"{got.get(reason, 0)} of {n}")
        record("no other reject reasons", set(got) == set(expected), ", ".join(sorted(set(got) - set(expected))))
        record("every raw row accounted for", accounted(s))

        Path(tmp, "broken.csv").write_text("not,the,right,columns\n1,2,3,4\n", encoding="utf-8")
        try:
            pipeline.build(Path(tmp, "broken.csv"), db)
            message = "it built"
        except SystemExit as e:
            message = str(e)
        with duckdb.connect(str(db), read_only=True) as check:
            kept = check.execute("SELECT count(*) FROM invoices").fetchone()[0]
        record("a failed rebuild says so and leaves the previous database intact",
               message.startswith("Build failed") and kept == 1, message[:60])


def reference_answers(con: duckdb.DuckDBPyConnection, ref: dict) -> None:
    def one(sql: str):
        return con.execute(sql).fetchone()[0]

    print("\nReference answers (independent stdlib recompute vs SQL on the cleaned tables)")

    def money(name: str, key: str, sql: str) -> None:
        got = float(one(sql))
        record(name, abs(got - ref[key]) <= 0.05, f"sql {got:,.2f} | reference {ref[key]:,.2f}")

    def exact(name: str, key: str, sql: str, fetch=None) -> None:
        got = fetch(con.execute(sql)) if fetch else one(sql)
        record(name, got == ref[key], f"sql {got} | reference {ref[key]}")

    money("Q1 paid revenue 2024", "q1_paid_revenue_2024",
          "SELECT sum(amount_usd) FROM invoices WHERE status = 'paid' AND year(invoice_date) = 2024")
    top_region = "FROM accounts WHERE is_active GROUP BY region ORDER BY avg(mrr_usd) DESC LIMIT 1"
    exact("Q2 top region by average MRR", "q2_top_region", f"SELECT region {top_region}")
    money("Q2 that region's average MRR", "q2_top_region_avg_mrr", f"SELECT avg(mrr_usd) {top_region}")
    money("Q3 refund total", "q3_refund_total", "SELECT -sum(amount_usd) FROM invoices WHERE status = 'refunded'")
    exact("Q3 refund count", "q3_refund_count", "SELECT count(*) FROM invoices WHERE status = 'refunded'")
    churn = "SELECT round(100.0 * count(*) FILTER (WHERE churned) / count(*), 2) FROM accounts WHERE plan = "
    exact("Q4 enterprise churn %", "q4_enterprise_churn_pct", churn + "'enterprise'")
    exact("Q4 starter churn %", "q4_starter_churn_pct", churn + "'starter'")
    top5 = "SELECT account_id FROM invoices GROUP BY account_id ORDER BY sum(recognized_usd) DESC, account_id LIMIT 5"
    exact("Q5 top 5 accounts by revenue", "q5_top5_accounts", top5, lambda cur: [r[0] for r in cur.fetchall()])
    exact("Q5 which of them have CSAT <= 2", "q5_top5_low_csat",
          f"SELECT account_id FROM accounts WHERE csat_score <= 2 AND account_id IN ({top5}) ORDER BY 1",
          lambda cur: [r[0] for r in cur.fetchall()])
    stuck = "FROM invoices WHERE status IN ('pending', 'failed')"
    exact("Q6 pending/failed count", "q6_stuck_count", f"SELECT count(*) {stuck}")
    money("Q6 pending/failed exposure", "q6_stuck_exposed", f"SELECT sum(amount_usd) {stuck}")


def guardrails(con: duckdb.DuckDBPyConnection) -> None:
    print("\nGuardrails")
    read_file = f"SELECT * FROM read_csv('{config.RAW_CSV.as_posix()}')"
    for name, sql in [
        ("DROP TABLE", "DROP TABLE invoices"),
        ("two statements", "SELECT 1; SELECT 2"),
        ("reading a file from disk", read_file),
        ("PRAGMA database_list", "PRAGMA database_list"),
        ("a system table function", "SELECT * FROM duckdb_settings()"),
        ("the rejects table", "SELECT count(*) FROM rejects"),
        ("the contacts table (emails)", "SELECT string_agg(contact_email, ';') FROM contacts"),
        ("contacts reached through a subquery", "SELECT (SELECT count(*) FROM contacts) FROM invoices LIMIT 1"),
        ("a CTE reusing a real table name", "WITH contacts AS (SELECT 1 AS x) SELECT * FROM contacts"),
    ]:
        try:
            ask.run_sql(con, sql)
            record(f"blocked: {name}", False, "it ran")
        except (ValueError, duckdb.Error) as e:
            record(f"blocked: {name}", True, str(e).splitlines()[0][:60])

    try:  # second layer: even without the guard, the engine refuses file access
        con.execute(read_file)
        record("engine sandbox refuses file reads on its own", False, "it ran")
    except duckdb.Error as e:
        record("engine sandbox refuses file reads on its own", True, str(e).splitlines()[0][:60])

    try:  # the guard must not block ordinary analysis
        ask.run_sql(con, """WITH rev AS (SELECT account_id, sum(recognized_usd) AS usd FROM invoices GROUP BY 1)
                            SELECT a.region, sum(rev.usd) FROM rev JOIN accounts a USING (account_id) GROUP BY 1""")
        record("allowed: a CTE joining invoices and accounts", True)
    except (ValueError, duckdb.Error) as e:
        record("allowed: a CTE joining invoices and accounts", False, str(e)[:60])

    try:
        with ask.connect() as second:
            second.execute("SELECT count(*) FROM invoices")
        record("a second connection works while the first is open", True)
    except duckdb.Error as e:
        record("a second connection works while the first is open", False, str(e)[:60])

    saved, config.QUERY_TIMEOUT = config.QUERY_TIMEOUT, 1
    try:
        ask.run_sql(con, "SELECT count(*) FROM invoices a, invoices b, invoices c WHERE a.amount + b.amount + c.amount > 0")
        record("a runaway query is stopped by the timeout", False, "it finished")
    except ValueError as e:
        record("a runaway query is stopped by the timeout", "ran longer" in str(e), str(e)[:60])
    finally:
        config.QUERY_TIMEOUT = saved


def same(cell, want, exp: dict) -> bool:
    if isinstance(want, str):
        return isinstance(cell, str) and cell.strip().lower() == want.lower()
    if isinstance(cell, bool) or not isinstance(cell, (int, float, Decimal)):
        return False
    got, tol = abs(float(cell)), exp.get("tol", 0)
    return abs(got - want) <= tol or bool(exp.get("pct") and abs(got * 100 - want) <= tol)


def judge(case: dict, res: dict, ref: dict) -> tuple[bool, str]:
    """Lenient on purpose and documented as such: passes when the expected values appear in the rows."""
    if case.get("refuse"):
        return res["state"] == "refused", res["state"]
    if res["state"] != "answered":
        return False, f"{res['state']}: {res.get('error') or res.get('explanation')}"[:160]
    rows = res["rows"]
    cells = [v for row in rows for v in row]
    for where, pool in (("first_row", list(rows[0]) if rows else []), ("contains", cells)):
        for exp in case.get(where, []):
            if not any(same(v, ref[exp["ref"]], exp) for v in pool):
                return False, f"{where}: no {exp['ref']} = {ref[exp['ref']]}"
    if "ordered" in case:
        want = ref[case["ordered"]]
        got = [v for v in cells if v in want]
        if got != want:
            return False, f"expected {want}, got {got}"
    return True, ""


def natural_language(ref: dict) -> None:
    print("\nNatural-language cases (live model)")
    seconds = cost = 0.0
    for case in json.loads(CASES.read_text(encoding="utf-8")):
        res = ask.answer(case["question"])
        seconds += res.get("seconds", 0)
        cost += res.get("cost_usd", 0)
        ok, detail = judge(case, res, ref)
        # An outage is not the predicted failure: an expected-fail case only counts when the model answered.
        xfail = case.get("xfail") if res["state"] != "failed" else None
        record(case["id"], ok, detail, xfail)
        if not ok and res.get("sql"):
            print("         sql: " + " ".join(res["sql"].split())[:300])
    print(f"  model time {seconds:.0f}s, cost ${cost:.2f}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if config.FX_MODE != "divide" or (config.FX_RATES["EUR"], config.FX_RATES["GBP"]) != (1.08, 1.27):
        raise SystemExit("Evals are defined for the default config (FX_MODE=divide, EUR 1.08, GBP 1.27).")
    summary = pipeline.build()
    ref = reference.answers(config.RAW_CSV)
    with ask.connect() as con:
        invariants(con, summary, ref)
        reject_paths()
        reference_answers(con, ref)
        guardrails(con)
    if "--offline" not in sys.argv[1:]:
        natural_language(ref)
    print("\n" + "  ".join(f"{k} {v}" for k, v in tally.items()))
    if tally["XPASS"]:
        print("An expected-fail case passed. Re-check it and update specs/04-evals.md.")
    return 1 if tally["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
