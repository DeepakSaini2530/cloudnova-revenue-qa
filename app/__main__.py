"""CLI. See specs/03-ask-contract.md.

    python -m app                 rebuild, then ask questions interactively
    python -m app "question"      rebuild, answer one question
    python -m app --build         rebuild and print the data-quality summary only
"""
import sys
from decimal import Decimal

from . import ask, pipeline


def fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (float, Decimal)):
        return f"{v:,.2f}"
    return str(v)  # integers stay plain: a year must print as 2024, not 2,024


def render(res: dict) -> None:
    if res["state"] == "failed":
        print(f"\nFAILED: {res['error']}")
        if res.get("sql"):
            print(f"\nLast SQL tried:\n{res['sql']}")
        return
    if res["state"] == "refused":
        print(f"\nCannot answer from this data: {res['explanation']}")
    else:
        table = [res["columns"]] + [[fmt(v) for v in row] for row in res["rows"]]
        widths = [max(len(r[i]) for r in table) for i in range(len(res["columns"]))]
        print(f"\nSQL:\n{res['sql']}\n")
        for n, row in enumerate(table):
            print("  " + "  ".join(cell.ljust(w) for cell, w in zip(row, widths)))
            if n == 0:
                print("  " + "  ".join("-" * w for w in widths))
        if len(res["rows"]) == ask.MAX_ROWS:
            print(f"  (first {ask.MAX_ROWS} rows)")
        print(f"\nHow: {res['explanation']}")
    print(f"\n[model: {res['seconds']:.1f}s, ${res['cost_usd']:.4f}]")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = sys.argv[1:]
    pipeline.print_summary(pipeline.build())
    if args == ["--build"]:
        return 0
    if args:
        res = ask.answer(" ".join(args))
        render(res)
        return 1 if res["state"] == "failed" else 0
    print("\nAsk a question about CloudNova revenue (blank line to quit).")
    while True:
        try:
            question = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question:
            break
        render(ask.answer(question))
    return 0


if __name__ == "__main__":
    sys.exit(main())
