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


def offline(con: duckdb.DuckDBPyConnection, summary: dict, ref: dict) -> None:
    def one(sql: str):
        return con.execute(sql).fetchone()[0]

    print("\nPipeline invariants")
    record("5,125 raw rows in", summary["raw_rows"] == 5125, str(summary["raw_rows"]))
    record("5,000 invoices out", summary["invoices"] == 5000 == ref["invoices"], str(summary["invoices"]))
    record("0 rejects", summary["rejects"] == 0, str(summary["rejects"]))
    record("every raw row accounted for",
           one("SELECT sum(source_rows) FROM invoices") + summary["rejects"] == summary["raw_rows"])
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
    record("every amount reconciles to list price (justifies FX divide)", one("""
        SELECT count(*) FROM invoices
        WHERE abs(abs(amount_usd) - mrr_usd * CASE WHEN billing_cycle = 'annual' THEN 10 ELSE 1 END) > 1""") == 0)
    record("accounts: one row per account_id",
           one("SELECT count(*) FROM accounts") == one("SELECT count(DISTINCT account_id) FROM invoices")
           == ref["accounts"])

    # The provided file produces no rejects, so prove the reject path with rows that must fail.
    header, good = config.RAW_CSV.read_text(encoding="utf-8").splitlines()[:2]
    with tempfile.TemporaryDirectory() as tmp:
        bad = Path(tmp, "bad.csv")
        bad.write_text("\n".join([header, good,
                                  good.replace("INV-104380", "INV-BAD-1").replace("STARTER", "Platinum"),
                                  good.replace("INV-104380", "INV-BAD-2").replace("52920.00", "52920.00x")]),
                       encoding="utf-8")
        s = pipeline.build(bad, Path(tmp, "bad.duckdb"))
    record("bad rows go to rejects with a reason, good row survives",
           s["invoices"] == 1 and sorted(r for r, _ in s["reject_reasons"]) == ["unknown plan", "unparseable amount"],
           f"{s['invoices']} invoice, rejects {s['reject_reasons']}")

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

    print("\nGuardrails")
    for name, sql in [("DROP TABLE is blocked", "DROP TABLE invoices"),
                      ("two statements are blocked", "SELECT 1; SELECT 2"),
                      ("reading a file from disk is blocked", f"SELECT * FROM read_csv('{config.RAW_CSV.as_posix()}')")]:
        try:
            ask.run_sql(con, sql)
            record(name, False, "it ran")
        except (ValueError, duckdb.Error) as e:
            record(name, True, str(e).splitlines()[0][:70])


def same(cell, want, exp: dict) -> bool:
    if isinstance(want, str):
        return isinstance(cell, str) and cell.strip().lower() == want.lower()
    if isinstance(cell, bool) or not isinstance(cell, (int, float, Decimal)):
        return False
    got, tol = abs(float(cell)), exp.get("tol", 0)
    return abs(got - want) <= tol or bool(exp.get("pct") and abs(got * 100 - want) <= tol)


def judge(case: dict, res: dict, ref: dict) -> tuple[bool, str]:
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
        record(case["id"], ok, detail, case.get("xfail"))
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
        offline(con, summary, ref)
    if "--offline" not in sys.argv[1:]:
        natural_language(ref)
    print("\n" + "  ".join(f"{k} {v}" for k, v in tally.items()))
    if tally["XPASS"]:
        print("An expected-fail case passed. Re-check it and update specs/04-evals.md.")
    return 1 if tally["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
