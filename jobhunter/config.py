"""Environment, constants and default settings.

Settings that the user may tune live in the Sheet's Settings tab; DEFAULT_SETTINGS
is the fallback used when the Sheet is unreachable (dry runs) and the seed written
on first setup.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

LOCAL_DIR = ROOT / "local"          # gitignored scratch output
PORTAL_DATA = ROOT / "portal" / "data"

# Bristol city centre; radius checks use this.
BRISTOL_LAT, BRISTOL_LON = 51.4545, -2.5879

# UWE postgraduate 2026/27: full-time work is only allowed in these windows
# (UWE working-hours letter PG 2026/27) and after course completion.
VACATION_WINDOWS: list[tuple[date, date]] = [
    (date(2026, 12, 21), date(2027, 1, 3)),
    (date(2027, 3, 22), date(2027, 4, 4)),
]
COURSE_END = date(2027, 9, 30)

# National Living Wage from 1 April 2026.
NATIONAL_LIVING_WAGE = 12.71
FTE_HOURS = 37.5
STUDENT_MAX_HOURS = 20

DEFAULT_SETTINGS: dict = {
    "mode": "part_time",                       # part_time | full_time
    "keywords": "finance assistant, accounts assistant, assistant accountant, bookkeeper, "
                "payroll, treasury, payments, reconciliation, credit control, finance analyst, accountant",
    "title_include": "account, finance, treasury, payment, settlement, reconcil, bookkeep, payroll, "
                     "ledger, credit control, regulatory report, safeguarding, billing, audit, fp&a",
    "title_exclude": "director, head of, chief, sales, mortgage adviser, financial adviser, "
                     "financial advisor, teacher, lecturer, solicitor, nurse, driver",
    "radius_miles": 15,
    "pay_floor_hourly": NATIONAL_LIVING_WAGE,
    "min_fte_salary": 24000,
    "good_pay_hourly": 15.0,
    "max_days_old": 21,
    "sources_enabled": "reed, adzuna",
    "keep_unverified_hours": True,
    "max_london_office_days": 0,
    "include_remote": True,
}


@dataclass(frozen=True)
class Env:
    reed_key: str = os.getenv("REED_KEY", "")
    adzuna_id: str = os.getenv("ADZUNA_ID", "")
    adzuna_key: str = os.getenv("ADZUNA_KEY", "")
    gemini_key: str = os.getenv("GEMINI_KEY", "")
    sheet_secret: str = os.getenv("SHEET_SECRET", "")
    apps_script_url: str = os.getenv("APPS_SCRIPT_URL", "")
    github_user: str = os.getenv("GITHUB_USER", "")


ENV = Env()


def _as_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


def _as_list(v) -> list[str]:
    if isinstance(v, list):
        return [str(x).strip().lower() for x in v if str(x).strip()]
    return [p.strip().lower() for p in str(v).split(",") if p.strip()]


def coerce_settings(raw: dict | None) -> dict:
    """Merge Sheet values (all strings) over defaults and give each the right type."""
    s = dict(DEFAULT_SETTINGS)
    for k, v in (raw or {}).items():
        if k in s and v not in (None, ""):
            s[k] = v
    for k in ("radius_miles", "max_days_old", "max_london_office_days"):
        s[k] = int(float(s[k]))
    for k in ("pay_floor_hourly", "min_fte_salary", "good_pay_hourly"):
        s[k] = float(s[k])
    for k in ("keep_unverified_hours", "include_remote"):
        s[k] = _as_bool(s[k])
    for k in ("keywords", "title_include", "title_exclude", "sources_enabled"):
        s[k] = _as_list(s[k])
    s["mode"] = str(s["mode"]).strip().lower()
    return s
