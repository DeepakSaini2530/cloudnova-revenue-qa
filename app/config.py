"""All settings, read from the environment (a .env file is honoured). See .env.example."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _path(name: str, default: str) -> Path:
    p = Path(os.getenv(name, default))
    return p if p.is_absolute() else ROOT / p


RAW_CSV = _path("RAW_CSV", "data/cloudnova_invoices.csv")
DB_PATH = _path("DB_PATH", "build/cloudnova.duckdb")

FX_MODE = os.getenv("FX_MODE", "divide")
if FX_MODE not in ("divide", "multiply"):
    raise SystemExit(f"FX_MODE must be 'divide' or 'multiply', got {FX_MODE!r}")


def _positive(name: str, default: str, cast=float):
    raw = os.getenv(name, default)
    try:
        value = cast(raw)
    except ValueError:
        value = 0
    if value <= 0:
        raise SystemExit(f"{name} must be a number greater than 0, got {raw!r}")
    return value


FX_RATES = {"USD": 1.0, "EUR": _positive("FX_EUR", "1.08"), "GBP": _positive("FX_GBP", "1.27")}

# USD per seat per month, from BUSINESS_CONTEXT.md
LIST_PRICE = {"starter": 49, "pro": 99, "enterprise": 299}

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "sonnet")
CLAUDE_TIMEOUT = _positive("CLAUDE_TIMEOUT", "120", int)  # seconds to wait for one model call
QUERY_TIMEOUT = _positive("QUERY_TIMEOUT", "10", int)  # seconds before a generated query is interrupted
