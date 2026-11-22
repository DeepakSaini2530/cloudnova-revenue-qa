"""Independent recomputation of the six stakeholder answers, straight from the raw CSV.

Standard library only and no imports from app/, on purpose. If this file and the SQL
pipeline agree, two separate readings of specs/01 and specs/02 agree. Uses the default
config (FX divide, EUR 1.08, GBP 1.27).

    python -m evals.reference      # print the answers
"""
import csv
import re
from collections import defaultdict
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

CSV = Path(__file__).resolve().parent.parent / "data" / "cloudnova_invoices.csv"

PLAN = {"starter": "starter", "start": "starter", "tier 1": "starter",
        "pro": "pro", "professional": "pro", "tier 2": "pro",
        "enterprise": "enterprise", "ent": "enterprise", "tier 3": "enterprise"}
STATUS = {"paid": "paid", "complete": "paid", "completed": "paid", "success": "paid",
          "pending": "pending", "in_review": "pending", "awaiting": "pending",
          "failed": "failed", "declined": "failed", "error": "failed",
          "refunded": "refunded", "refund": "refunded", "charged_back": "refunded",
          "void": "void", "cancelled": "void", "canceled": "void"}
PRICE = {"starter": Decimal(49), "pro": Decimal(99), "enterprise": Decimal(299)}
RATE = {"USD": Decimal("1"), "EUR": Decimal("1.08"), "GBP": Decimal("1.27")}
PRECEDENCE = ["refunded", "void", "paid", "failed", "pending"]
DATE_FORMATS = ["%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d-%m-%Y", "%b %d %Y"]
CENT = Decimal("0.01")


def cents(x: Decimal) -> Decimal:
    return x.quantize(CENT, ROUND_HALF_UP)


def parse_date(text: str):
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            pass
    raise ValueError(f"unparseable date: {text!r}")


def load(path: Path = CSV) -> list[dict]:
    """Clean every raw row, then keep one row per invoice_id (latest lifecycle status wins)."""
    by_id = defaultdict(list)
    with open(path, encoding="utf-8", newline="") as f:
        for line, r in enumerate(csv.DictReader(f), start=2):
            plan = PLAN[r["plan"].strip().lower()]
            amount = Decimal(re.sub(r"[^0-9.\-]", "", r["amount"]))
            discount = Decimal(r["discount_pct"])
            by_id[r["invoice_id"]].append({
                "line": line,
                "invoice_id": r["invoice_id"],
                "account_id": r["account_id"],
                "region": r["region"].strip(),
                "plan": plan,
                "status": STATUS[r["status"].strip().lower()],
                "amount_usd": cents(amount / RATE[r["currency"].strip()]),
                "mrr": cents(PRICE[plan] * int(r["seats"]) * (1 - discount / 100)),
                "invoice_date": parse_date(r["invoice_date"]),
                "churned": r["churned"].strip().lower() in ("true", "yes", "1", "y"),
                "csat": int(r["csat_score"]) if r["csat_score"].strip() else None,
                "dash_ambiguous": bool(re.fullmatch(r"(0[1-9]|1[0-2])-(0[1-9]|1[0-2])-\d{4}", r["invoice_date"].strip())),
            })
    invoices = []
    for copies in by_id.values():
        winner = min(copies, key=lambda c: (PRECEDENCE.index(c["status"]), c["line"]))
        winner["status_conflict"] = len({c["status"] for c in copies}) > 1
        invoices.append(winner)
    return invoices


def answers(path: Path = CSV) -> dict:
    inv = load(path)
    latest = {}  # account_id -> its latest invoice
    for i in sorted(inv, key=lambda i: (i["invoice_date"], i["invoice_id"])):
        latest[i["account_id"]] = i

    paid_2024 = sum(i["amount_usd"] for i in inv if i["status"] == "paid" and i["invoice_date"].year == 2024)

    by_region = defaultdict(list)
    for a in latest.values():
        if not a["churned"]:
            by_region[a["region"]].append(a["mrr"])
    avg_mrr = {reg: cents(sum(v) / len(v)) for reg, v in by_region.items()}
    top_region = max(avg_mrr, key=avg_mrr.get)

    refunds = [i for i in inv if i["status"] == "refunded"]
    stuck = [i for i in inv if i["status"] in ("pending", "failed")]

    churn = {}
    for plan in ("starter", "pro", "enterprise"):
        accts = [a for a in latest.values() if a["plan"] == plan]
        churn[plan] = round(100 * sum(a["churned"] for a in accts) / len(accts), 2)

    revenue = defaultdict(Decimal)
    for i in inv:
        if i["status"] in ("paid", "refunded"):
            revenue[i["account_id"]] += i["amount_usd"]
    top5 = sorted(revenue, key=lambda a: (-revenue[a], a))[:5]

    return {
        "invoices": len(inv),
        "accounts": len(latest),
        "status_conflicts": sum(i["status_conflict"] for i in inv),
        "ambiguous_invoice_dates": sum(i["dash_ambiguous"] for i in inv),
        "q1_paid_revenue_2024": float(paid_2024),
        "q2_top_region": top_region,
        "q2_top_region_avg_mrr": float(avg_mrr[top_region]),
        "q3_refund_total": float(-sum(i["amount_usd"] for i in refunds)),
        "q3_refund_count": len(refunds),
        "q4_enterprise_churn_pct": churn["enterprise"],
        "q4_starter_churn_pct": churn["starter"],
        "q5_top5_accounts": top5,
        "q5_top5_low_csat": [a for a in top5 if latest[a]["csat"] is not None and latest[a]["csat"] <= 2],
        "q6_stuck_count": len(stuck),
        "q6_stuck_exposed": float(sum(i["amount_usd"] for i in stuck)),
    }


if __name__ == "__main__":
    for key, value in answers().items():
        print(f"{key:28} {value}")
