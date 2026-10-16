"""Adzuna Jobs API (free: 25/min, 250/day). Docs: https://developer.adzuna.com/docs/search

Adzuna normalises every salary to an annual figure and marks guesses with
`salary_is_predicted`. Descriptions are snippets; there is no detail endpoint.
"""
from __future__ import annotations

import httpx

from ..config import ENV
from ..models import Job, parse_uk_date, strip_html
from . import SourceContext, safe_err

NAME = "adzuna"
BASE = "https://api.adzuna.com/v1/api/jobs/gb/search"
CATEGORY = "accounting-finance-jobs"


def _to_job(item: dict) -> Job:
    ct = item.get("contract_time")
    ctype = item.get("contract_type")
    loc = item.get("location") or {}
    return Job(
        source=NAME,
        external_id=str(item["id"]),
        title=strip_html(item.get("title") or ""),
        company=((item.get("company") or {}).get("display_name") or "").strip(),
        location=(loc.get("display_name") or "").strip(),
        latitude=item.get("latitude"),
        longitude=item.get("longitude"),
        contract_time="part_time" if ct == "part_time" else "full_time" if ct == "full_time" else "unknown",
        contract_type="permanent" if ctype == "permanent" else "contract" if ctype == "contract" else "unknown",
        salary_min=item.get("salary_min") or None,
        salary_max=item.get("salary_max") or None,
        salary_period="year" if (item.get("salary_min") or item.get("salary_max")) else "unknown",
        salary_predicted=str(item.get("salary_is_predicted")) == "1",
        url=item.get("redirect_url") or "",
        posted=parse_uk_date(item.get("created")),
        description=strip_html(item.get("description") or ""),
    )


def _search(ctx: SourceContext, settings: dict, **params) -> list[dict]:
    q = {
        "app_id": ENV.adzuna_id,
        "app_key": ENV.adzuna_key,
        "results_per_page": 50,
        "max_days_old": settings["max_days_old"],
        "category": CATEGORY,
        "sort_by": "date",
        "content-type": "application/json",
    }
    q.update(params)
    out: list[dict] = []
    for page in (1, 2):
        r = ctx.get(f"{BASE}/{page}", params=q)
        results = r.json().get("results", [])
        out.extend(results)
        if len(results) < 50:
            break
    return out


def fetch(settings: dict, ctx: SourceContext) -> list[Job]:
    if not (ENV.adzuna_id and ENV.adzuna_key):
        ctx.errors.append("adzuna: ADZUNA_ID/ADZUNA_KEY missing")
        return []
    seen: dict[str, Job] = {}
    part_time_mode = settings["mode"] == "part_time"
    km = int(settings["radius_miles"] * 1.609)

    for kw in settings["keywords"]:
        passes = [{"what": kw, "where": "Bristol", "distance": km}]
        if part_time_mode:
            passes[0]["part_time"] = 1
        if settings["include_remote"]:
            p = {"what": kw, "what_and": "remote"}
            if part_time_mode:
                p["part_time"] = 1
            passes.append(p)
        for p in passes:
            try:
                for item in _search(ctx, settings, **p):
                    job = _to_job(item)
                    if job.url:
                        seen.setdefault(job.job_id, job)
            except httpx.HTTPError as e:
                ctx.errors.append(f"adzuna {p}: {safe_err(e)}")
    return list(seen.values())
