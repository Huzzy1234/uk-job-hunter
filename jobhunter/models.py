"""The Job record every source normalises into, plus text-parsing helpers."""
from __future__ import annotations

import math
import re
from datetime import date, datetime
from typing import Literal

from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from .config import FTE_HOURS

Remote = Literal["remote", "hybrid", "onsite", "unknown"]
ContractTime = Literal["part_time", "full_time", "unknown"]
ContractType = Literal["permanent", "temp", "contract", "unknown"]
SalaryPeriod = Literal["hour", "day", "week", "month", "year", "unknown"]
EmployerType = Literal["agency", "direct", "unknown"]


class Job(BaseModel):
    source: str
    external_id: str
    title: str
    url: str
    company: str = ""
    location: str = ""
    latitude: float | None = None
    longitude: float | None = None
    remote: Remote = "unknown"
    contract_time: ContractTime = "unknown"
    contract_type: ContractType = "unknown"
    hours_per_week: float | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_period: SalaryPeriod = "unknown"
    salary_predicted: bool = False
    posted: date | None = None
    expires: date | None = None
    description: str = ""
    employer_type: EmployerType = "unknown"
    detailed: bool = False          # full description fetched (vs. snippet)

    # Derived by rules / scoring
    tags: list[str] = Field(default_factory=list)
    drop_reason: str | None = None
    score: int | None = None
    why: str = ""
    red_flags: list[str] = Field(default_factory=list)

    @property
    def job_id(self) -> str:
        return f"{self.source}:{self.external_id}"

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.location}\n{self.description}".lower()

    def tag(self, *tags: str) -> None:
        for t in tags:
            if t not in self.tags:
                self.tags.append(t)

    # -- derived views -----------------------------------------------------
    def hourly_estimate(self) -> float | None:
        """Best guess at an hourly rate, converting annual/daily figures at FTE."""
        amount = self.salary_min or self.salary_max
        if not amount:
            return None
        period = self.salary_period if self.salary_period != "unknown" else infer_period(amount)
        if period == "hour":
            return amount
        if period == "day":
            return amount / 7.5
        if period == "week":
            return amount / FTE_HOURS
        if period == "month":
            return amount * 12 / 52 / FTE_HOURS
        return amount / 52 / FTE_HOURS

    def pay_text(self) -> str:
        lo, hi = self.salary_min, self.salary_max
        if not lo and not hi:
            return ""
        period = self.salary_period if self.salary_period != "unknown" else infer_period(lo or hi)
        unit = {"hour": "/hr", "day": "/day", "week": "/wk", "month": "/mo", "year": "/yr"}[period]

        def fmt(x: float) -> str:
            return f"£{x:,.2f}".rstrip("0").rstrip(".") if x < 1000 else f"£{x:,.0f}"

        if lo and hi and abs(lo - hi) > 0.01:
            s = f"{fmt(lo)}–{fmt(hi)}{unit}"
        else:
            s = f"{fmt(lo or hi)}{unit}"
        if period == "year":
            s += " FTE"
        if self.salary_predicted:
            s += " (est.)"
        return s

    def hours_text(self) -> str:
        parts = []
        if self.hours_per_week:
            parts.append(f"{self.hours_per_week:g} hrs/wk")
        elif self.contract_time != "unknown":
            parts.append(self.contract_time.replace("_", "-"))
        if self.contract_type != "unknown":
            parts.append(self.contract_type)
        if "vacation-only" in self.tags:
            parts.append("vacation window only")
        if "hours-unverified" in self.tags:
            parts.append("hours: confirm")
        return ", ".join(parts)


# --------------------------------------------------------------------------
# Parsing helpers (pure functions, unit-tested)
# --------------------------------------------------------------------------

def strip_html(html: str) -> str:
    if not html:
        return ""
    if "<" not in html:
        return re.sub(r"\s+", " ", html).strip()
    text = BeautifulSoup(html, "lxml").get_text(" ")
    return re.sub(r"\s+", " ", text).strip()


def parse_uk_date(s: str | None) -> date | None:
    """'14/09/2026' -> date; also accepts ISO strings."""
    if not s:
        return None
    s = s.strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def infer_period(amount: float) -> SalaryPeriod:
    if amount < 150:
        return "hour"
    if amount < 1500:
        return "day"
    if amount < 6000:
        return "month"
    return "year"


_WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}

_HOURS_RANGE = re.compile(
    r"(\d{1,2}(?:\.\d)?)\s*(?:-|–|to)\s*(\d{1,2}(?:\.\d)?)\s*(?:hours|hrs)\b(?:\s*(?:per|a|/|each)\s*week)?",
    re.I,
)
_HOURS_ONE = re.compile(
    r"(\d{1,2}(?:\.\d)?)\s*(?:hours|hrs|hour)\s*(?:per|a|/|each)\s*(?:week|wk)|"
    r"(\d{1,2}(?:\.\d)?)\s*(?:hours|hrs)\s*(?:pw|p/w)\b|"
    r"(\d{1,2}(?:\.\d)?)\s*(?:hour|hr)\s*(?:per|a)?\s*week",
    re.I,
)
_DAYS = re.compile(r"\b(\d|one|two|three|four|five)\s*(?:-|to)?\s*(\d|one|two|three|four|five)?\s*days?\s*(?:per|a|/|each)\s*week", re.I)
_FTE = re.compile(r"\b(0\.\d{1,2})\s*fte\b", re.I)


def detect_hours(text: str) -> float | None:
    """Hours per week stated in free text, or None."""
    t = text.lower()
    m = _HOURS_RANGE.search(t)
    if m:
        return max(float(m.group(1)), float(m.group(2)))
    m = _HOURS_ONE.search(t)
    if m:
        return float(next(g for g in m.groups() if g))
    m = _FTE.search(t)
    if m:
        return round(float(m.group(1)) * FTE_HOURS, 1)
    m = _DAYS.search(t)
    if m:
        def num(g):
            return None if g is None else (_WORD_NUM.get(g.lower()) or int(g))
        a, b = num(m.group(1)), num(m.group(2))
        days = max(x for x in (a, b) if x is not None)
        return days * 7.5
    return None


_PART_TIME = re.compile(r"part[- ]time|job[- ]share|term[- ]time|school hours|reduced hours", re.I)
_FULL_TIME = re.compile(r"full[- ]time|\b37\.5\s*(hours|hrs)|\b40\s*(hours|hrs)", re.I)
_TEMP = re.compile(r"\btemp(orary|s)?\b|\binterim\b|fixed[- ]term|\bftc\b|maternity cover|bank staff|\blocum\b|short[- ]term|seasonal|christmas temp", re.I)
_PERM = re.compile(r"\bpermanent\b|\bperm\b", re.I)
_SELF_EMPLOYED = re.compile(
    r"self[- ]employed|freelance|sole trader|outside ir35|umbrella company|\bltd company\b|own limited company|"
    r"commission[- ]only|day[- ]rate contractor|independent contractor",
    re.I,
)
_PAYE = re.compile(r"\bpaye\b|through (the|our) agency|agency worker|temporary worker|weekly pay", re.I)
_REMOTE = re.compile(
    r"fully remote|100% remote|home[- ]based|work(ing)? from home|\bwfh\b|home[- ]working|"
    r"remote[- ](first|only|working|role|position|job|opportunity|basis)|this (role|position) is remote|"
    r"(role|position|job) is (fully )?remote",
    re.I,
)
_REMOTE_TITLE = re.compile(r"\bremote\b|\bwfh\b|home[- ]based", re.I)
_HYBRID = re.compile(r"\bhybrid\b|\d\s*days?\s*(in|at)\s*(the\s*)?office|office\s*\d\s*days", re.I)
_LONDON = re.compile(r"\blondon\b|\bEC[1-4]|\bWC[12]|\bE1\b|\bSE1\b", re.I)


def detect_contract_time(text: str) -> ContractTime:
    if _PART_TIME.search(text):
        return "part_time"
    if _FULL_TIME.search(text):
        return "full_time"
    return "unknown"


def detect_contract_type(text: str) -> ContractType:
    if _TEMP.search(text):
        return "temp"
    if _PERM.search(text):
        return "permanent"
    return "unknown"


def looks_self_employed(text: str) -> bool:
    return bool(_SELF_EMPLOYED.search(text)) and not _PAYE.search(text)


def detect_remote(text: str, location: str = "", title: str = "") -> Remote:
    """Bare 'remote' in a description is not enough (e.g. 'occasional remote working')."""
    loc = location.lower()
    if "remote" in loc or "home based" in loc or "work from home" in loc:
        return "remote"
    if _HYBRID.search(text):
        return "hybrid"
    if _REMOTE_TITLE.search(title) or _REMOTE.search(text):
        return "remote"
    return "unknown"


def mentions_london(text: str) -> bool:
    return bool(_LONDON.search(text))


def miles_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def enrich_from_text(job: Job) -> Job:
    """Fill unknown fields from the title/description text. Never overrides explicit data."""
    text = job.text
    if job.hours_per_week is None:
        job.hours_per_week = detect_hours(text)
    if job.contract_time == "unknown":
        job.contract_time = detect_contract_time(text)
        if job.contract_time == "unknown" and job.hours_per_week:
            job.contract_time = "part_time" if job.hours_per_week <= 30 else "full_time"
    if job.contract_type == "unknown":
        job.contract_type = detect_contract_type(text)
    if job.remote == "unknown":
        job.remote = detect_remote(text, job.location, job.title)
    if job.salary_period == "unknown" and (job.salary_min or job.salary_max):
        job.salary_period = infer_period(job.salary_min or job.salary_max)
    return job
