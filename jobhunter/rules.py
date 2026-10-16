"""Deterministic filters. Each returns None to keep, or a short drop reason.

Order matters: the first rule that drops wins, and its reason is what the
Dropped tab shows. Tags are added along the way for scoring and display.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from .config import (
    BRISTOL_LAT,
    BRISTOL_LON,
    COURSE_END,
    STUDENT_MAX_HOURS,
    VACATION_WINDOWS,
)
from .models import Job, looks_self_employed, mentions_london, miles_between

BRISTOL_AREA = re.compile(
    r"\b(bristol|bath|clifton|filton|keynsham|portishead|yate|bradley stoke|avonmouth|emersons green|"
    r"thornbury|nailsea|clevedon|patchway|almondsbury|aztec west|kingswood|bedminster|redland|"
    r"stoke gifford|severn beach|chipping sodbury|saltford|portbury|pill|long ashton|henbury|"
    r"southmead|frenchay|fishponds|brislington|whitchurch|hanham|warmley|mangotsfield|downend|"
    r"winterbourne|frampton cotterell|backwell|congresbury|chew magna|pensford|midsomer norton|radstock|"
    r"north somerset|south gloucestershire|somerset|avon)\b|\bbs\d{1,2}\b|\bba[12]\b",
    re.I,
)

DURATION_SHORT = re.compile(r"\b([1-4])\s*weeks?\b|\b(1|2|3)\s*months?\b|christmas|festive|easter|half[- ]term", re.I)


def today() -> date:
    return date.today()


def days_to_next_vacation(d: date | None = None) -> int | None:
    """Days until the next window opens (0 if inside one, None if none left before course end)."""
    d = d or today()
    for start, end in VACATION_WINDOWS:
        if start <= d <= end:
            return 0
        if d < start:
            return (start - d).days
    if d >= COURSE_END:
        return 0
    return None


# ---- individual rules ----------------------------------------------------

def rule_title(job: Job, s: dict) -> str | None:
    t = job.title.lower()
    if not any(k in t for k in s["title_include"]):
        return "title not a finance role"
    for k in s["title_exclude"]:
        if k in t:
            return f"title excluded ({k})"
    return None


def rule_freshness(job: Job, s: dict) -> str | None:
    if job.posted and (today() - job.posted).days > s["max_days_old"]:
        return f"older than {s['max_days_old']} days"
    if job.expires and job.expires < today():
        return "expired"
    return None


def rule_legal_hours(job: Job, s: dict) -> str | None:
    """Student visa: ≤20 h/week in term; full-time only inside a vacation window; never self-employed."""
    text = job.text
    if looks_self_employed(text):
        return "self-employed / freelance (not allowed on Student visa)"

    if s["mode"] == "full_time":
        return None

    hours = job.hours_per_week
    if hours is not None:
        if hours <= STUDENT_MAX_HOURS:
            job.tag("hours-ok")
            return None
        return _full_time_temp_or_drop(job, f"{hours:g} hrs/week is over the 20-hour limit")

    if job.contract_time == "part_time":
        job.tag("hours-unverified" if "part" not in job.title.lower() else "hours-ok")
        return None

    if job.contract_time == "full_time":
        return _full_time_temp_or_drop(job, "full-time")

    # Hours unknown. Temp/interim roles may be flexible; keep if the user allows it.
    if job.contract_type in ("temp", "contract") and s["keep_unverified_hours"]:
        job.tag("hours-unverified")
        return None
    if job.contract_type == "permanent":
        return "permanent role with no part-time hours stated"
    if s["keep_unverified_hours"]:
        job.tag("hours-unverified")
        return None
    return "hours not stated"


def _full_time_temp_or_drop(job: Job, why: str) -> str | None:
    """A full-time job is only workable as a short temp inside a vacation window."""
    if job.contract_type == "permanent":
        return f"{why}, permanent"
    if job.contract_type not in ("temp", "contract"):
        return f"{why}, not a temp role"
    gap = days_to_next_vacation()
    if gap is None or gap > 35:
        return f"{why} temp, no vacation window within 5 weeks"
    if not DURATION_SHORT.search(job.text) and gap > 0:
        return f"{why} temp, not clearly a short vacation cover"
    job.tag("vacation-only")
    return None


def rule_location(job: Job, s: dict) -> str | None:
    text = job.text
    loc = job.location.lower()

    if job.remote == "remote":
        if s["include_remote"]:
            job.tag("remote")
            return None
        return "remote roles disabled"

    in_area = False
    if job.latitude and job.longitude:
        in_area = miles_between(job.latitude, job.longitude, BRISTOL_LAT, BRISTOL_LON) <= s["radius_miles"]
    if not in_area:
        in_area = bool(BRISTOL_AREA.search(loc)) or (not loc and bool(BRISTOL_AREA.search(text)))

    if job.remote == "hybrid":
        if in_area:
            job.tag("hybrid-bristol")
            return None
        if mentions_london(loc) or (not loc and mentions_london(text)):
            if s["max_london_office_days"] > 0:
                job.tag("hybrid-london")
                return None
            return "London hybrid (travel cost wipes part-time pay)"
        return "hybrid outside Bristol area"

    if in_area:
        job.tag("bristol")
        return None
    if mentions_london(loc):
        return "London, not remote"
    if loc:
        return f"outside Bristol area ({job.location[:40]})"
    return "location unknown"


def rule_pay(job: Job, s: dict) -> str | None:
    hourly = job.hourly_estimate()
    if hourly is None:
        job.tag("pay-unknown")
        return None
    if job.salary_predicted:
        job.tag("pay-estimated")
    if hourly < s["pay_floor_hourly"] - 0.01:
        return f"pay below £{s['pay_floor_hourly']:.2f}/hr (≈£{hourly:.2f})"
    if job.salary_period == "year" and (job.salary_max or job.salary_min or 0) < s["min_fte_salary"]:
        return f"FTE salary below £{s['min_fte_salary']:,.0f}"
    if hourly >= s["good_pay_hourly"]:
        job.tag("pay-good")
    return None


RULES = [rule_title, rule_freshness, rule_legal_hours, rule_location, rule_pay]


def evaluate(job: Job, settings: dict) -> Job:
    """Apply all rules in order; sets job.drop_reason (None = keep) and tags."""
    job.drop_reason = None
    for rule in RULES:
        reason = rule(job, settings)
        if reason:
            job.drop_reason = reason
            break
    return job


# ---- interim scoring until Gemini is wired in -----------------------------

STRONG = re.compile(r"treasury|payment|settlement|reconcil|regulatory|safeguard|fx\b|liquidity|fintech", re.I)
CORE = re.compile(r"accounts assistant|finance assistant|assistant accountant|bookkeep|payroll|ledger|credit control|finance analyst", re.I)


def heuristic_score(job: Job) -> int:
    score = 50
    t = job.title
    if STRONG.search(t):
        score += 20
    elif STRONG.search(job.description[:1500]):
        score += 8
    if CORE.search(t):
        score += 10
    if "pay-good" in job.tags:
        score += 10
    if "pay-unknown" in job.tags:
        score -= 5
    if "remote" in job.tags:
        score += 6
    if "hours-ok" in job.tags:
        score += 8
    if "hours-unverified" in job.tags:
        score -= 10
    if "vacation-only" in job.tags:
        score -= 5
    if job.employer_type == "direct":
        score += 5
    if job.posted and (today() - job.posted).days <= 3:
        score += 5
    return max(0, min(100, score))


def heuristic_why(job: Job) -> str:
    bits = []
    if STRONG.search(job.title):
        bits.append("matches his treasury/payments background")
    elif CORE.search(job.title):
        bits.append("core finance role he can do in his sleep")
    if job.hours_per_week:
        bits.append(f"{job.hours_per_week:g} hrs/week fits the visa")
    elif "hours-unverified" in job.tags:
        bits.append("confirm hours ≤20/week before applying")
    if "remote" in job.tags:
        bits.append("remote")
    elif "bristol" in job.tags:
        bits.append("local")
    if "pay-good" in job.tags:
        bits.append("pays well for part-time")
    return "; ".join(bits).capitalize()
