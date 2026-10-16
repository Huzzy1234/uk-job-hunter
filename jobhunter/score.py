"""Gemini scoring: how well does each kept job fit Nafiu, and is anything off?

Free tier only. Jobs are sent in small batches with a strict JSON schema, and a
failed or rate-limited call simply leaves the heuristic score in place. No
personal identifiers go into the prompt; the profile text comes from the
Sheet's Profile tab so he can edit his own priorities.
"""
from __future__ import annotations

import json
import time

import httpx

from .config import ENV, STUDENT_MAX_HOURS
from .models import Job

MODELS = ["gemini-2.5-flash", "gemini-3.5-flash-lite", "gemini-3.8-flash"]
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
BATCH = 6
PAUSE_S = 4.0

SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "id": {"type": "STRING"},
            "fit": {"type": "INTEGER", "description": "0-100 fit for this candidate"},
            "role_family": {"type": "STRING"},
            "hours_per_week": {"type": "NUMBER", "nullable": True, "description": "weekly hours if the advert states them, else null"},
            "hours_ok": {"type": "BOOLEAN", "description": "true unless the advert clearly requires more than 20 h/week in term time"},
            "why": {"type": "STRING", "description": "one sentence, max 160 chars, written to the candidate"},
            "red_flags": {"type": "ARRAY", "items": {"type": "STRING"}},
        },
        "required": ["id", "fit", "role_family", "hours_ok", "why", "red_flags"],
    },
}

DEFAULT_PROFILE = {
    "headline": "Payments treasury & settlement analyst, chartered accountant (ICAN), FCA regulatory reporting experience",
    "role_families_ranked": "1 treasury/liquidity/FX ops; 2 settlement, reconciliation, safeguarding; 3 regulatory reporting; "
                            "4 finance/accounts assistant, assistant accountant; 5 payroll, bookkeeping",
    "skills": "treasury, FX pricing, liquidity, settlement, bank/wallet reconciliation, FCA returns, month-end, "
              "management accounts, tax filings, payroll",
    "systems": "Xero, Sage 50, SAP S/4HANA, advanced Excel",
    "experience_summary": "4+ years in fintech treasury and finance, incl. a UK FCA-authorised payments firm (remote)",
    "constraints": f"UK Student visa: max {STUDENT_MAX_HOURS} h/week in term (hard cap), full-time only in short university "
                   "vacations; no self-employment; Bristol/Bath or remote UK only",
    "availability": "up to 20 h/week now; full-time from Oct 2027",
    "pay_expectation": "£15-20/hour part-time",
}

SYSTEM = """You score UK job adverts for one candidate. Be strict and practical: a good score means he should apply today.

Scoring guide (fit 0-100):
- 85-100: part-time/temp role squarely in treasury, payments, settlement, reconciliation, safeguarding or regulatory reporting; or a finance/accounts role at a fintech or payments firm.
- 65-84: finance assistant / assistant accountant / bookkeeping / payroll roles he is clearly qualified for and can do within 20 h/week.
- 40-64: finance roles that are a stretch (senior management, niche sector systems, practice audit seniors) or whose hours are unclear.
- 0-39: wrong level (director/head), wrong field, obviously full-time permanent, or unrealistic for a student on 20 h/week.
Penalise: adverts demanding specific UK qualifications he lacks (ACA/ACCA fully qualified, CIPP), 5+ years UK practice experience, driving licence essential, or "must be available full-time". Do not penalise lack of UK experience: he has FCA reporting experience at a UK payments firm.
hours_ok = false only if the advert clearly requires more than 20 hours a week in term time (e.g. "37.5 hours", "Mon-Fri 9-5", "full-time").
red_flags: short phrases, only things the candidate must check or that make the role risky (max 3).
why: one plain sentence addressed to him ("You've done exactly this at a payments firm...").
Return one object per job id, in the same order."""


def profile_text(profile: dict | None) -> str:
    p = dict(DEFAULT_PROFILE)
    p.update({k: v for k, v in (profile or {}).items() if v})
    return "\n".join(f"{k.replace('_', ' ')}: {v}" for k, v in p.items())


def _job_block(j: Job) -> str:
    desc = j.description[:1400]
    return (f"### id={j.job_id}\ntitle: {j.title}\ncompany: {j.company} ({j.employer_type})\nlocation: {j.location} "
            f"[{j.remote}]\nhours/contract: {j.hours_text() or 'not stated'}\npay: {j.pay_text() or 'not stated'}\n"
            f"tags: {', '.join(j.tags)}\ndescription: {desc}\n")


def _call(model: str, prompt: str) -> list[dict]:
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
            "responseSchema": SCHEMA,
            "maxOutputTokens": 8192,
        },
    }
    r = httpx.post(ENDPOINT.format(model=model), params={"key": ENV.gemini_key}, json=body, timeout=120)
    if r.status_code == 429:
        raise RateLimited(r.text[:200])
    r.raise_for_status()
    parts = r.json()["candidates"][0]["content"]["parts"]
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    return _parse_json_array(text)


def _parse_json_array(text: str) -> list[dict]:
    """Tolerate code fences, stray prose and a truncated final object."""
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("["):] if "[" in t else t
    start, end = t.find("["), t.rfind("]")
    if start >= 0 and end > start:
        t = t[start:end + 1]
    try:
        data = json.loads(t)
    except json.JSONDecodeError:
        # drop a truncated trailing object and close the array
        cut = t.rfind("},")
        if cut < 0:
            raise
        data = json.loads(t[:cut + 1] + "]")
    if not isinstance(data, list):
        raise ValueError("not a JSON array")
    return data


class RateLimited(Exception):
    pass


def score_jobs(jobs: list[Job], profile: dict | None = None, errors: list[str] | None = None) -> int:
    """Score in place. Returns the number of Gemini calls made."""
    if not ENV.gemini_key or not jobs:
        return 0
    errors = errors if errors is not None else []
    header = f"CANDIDATE PROFILE\n{profile_text(profile)}\n\nJOBS\n"
    calls = 0
    for i in range(0, len(jobs), BATCH):
        batch = jobs[i:i + BATCH]
        prompt = header + "\n".join(_job_block(j) for j in batch)
        result = None
        # each model gets two tries before falling through to the next
        plan = [m for m in MODELS for _ in (0, 1)]
        for attempt, model in enumerate(plan):
            try:
                calls += 1
                result = _call(model, prompt)
                break
            except RateLimited:
                time.sleep(15 * (attempt + 1))
            except (httpx.HTTPError, KeyError, ValueError) as e:
                errors.append(f"gemini {model}: {type(e).__name__}: {str(e).split(' for url')[0][:100]}")
                time.sleep(3)
        if not result:
            errors.append(f"gemini: batch {i // BATCH} unscored")
            continue
        by_id = {str(r.get("id")): r for r in result if isinstance(r, dict)}
        for j in batch:
            r = by_id.get(j.job_id)
            if not r:
                continue
            _apply(j, r)
        time.sleep(PAUSE_S)
    return calls


def _apply(j: Job, r: dict) -> None:
    fit = int(r.get("fit", j.score or 50))
    score = fit
    if "pay-good" in j.tags:
        score += 5
    if j.employer_type == "direct":
        score += 3
    if "hours-unverified" in j.tags:
        score -= 8
    if "remote" in j.tags:
        score += 3
    hours = r.get("hours_per_week")
    if hours and not j.hours_per_week:
        j.hours_per_week = float(hours)
    if r.get("hours_ok") is False or (j.hours_per_week and j.hours_per_week > STUDENT_MAX_HOURS and "vacation-only" not in j.tags):
        score = min(score, 25)
        j.red_flags.insert(0, "advert looks like more than 20 h/week – check before applying")
    j.score = max(0, min(100, score))
    j.why = (r.get("why") or j.why)[:200]
    for f in (r.get("red_flags") or [])[:3]:
        f = str(f).strip()
        if f and f not in j.red_flags:
            j.red_flags.append(f)
    j.tag("ai-scored")
    if r.get("role_family"):
        j.tag(f"family:{str(r['role_family'])[:30]}")
