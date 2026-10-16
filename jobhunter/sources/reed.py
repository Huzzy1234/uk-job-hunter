"""Reed.co.uk Jobseeker API (free key). Docs: https://www.reed.co.uk/developers/jobseeker

Search gives snippets; /jobs/{id} gives the full description plus explicit
part-time / contract / salary-type flags, so `enrich()` is called only for jobs
that survive the first filter pass and are new to the Sheet.
"""
from __future__ import annotations

import httpx

from ..config import ENV
from ..models import Job, parse_uk_date, strip_html
from . import SourceContext, safe_err

NAME = "reed"
BASE = "https://www.reed.co.uk/api/1.0"

AGENCY_HINTS = ("recruitment", "recruit", "staffing", "resourcing", "personnel", "hays", "reed",
                "robert half", "michael page", "page personnel", "adecco", "pertemps", "office angels",
                "randstad", "manpower", "brook street", "search consultancy", "core3", "artis",
                "juice", "alexander mae", "element", "ashley rees", "sheridan maine", "cmd recruitment",
                "get staffed", "morgan mckinley", "robert walters", "sellick", "cma recruitment")


def _auth() -> tuple[str, str]:
    return (ENV.reed_key, "")


def _employer_type(name: str) -> str:
    n = name.lower()
    return "agency" if any(h in n for h in AGENCY_HINTS) else "direct"


def _salary_period(raw: str | None) -> str:
    r = (raw or "").lower()
    if "hour" in r:
        return "hour"
    if "day" in r:
        return "day"
    if "week" in r:
        return "week"
    if "month" in r:
        return "month"
    if "annum" in r or "year" in r:
        return "year"
    return "unknown"


def _to_job(item: dict) -> Job:
    return Job(
        source=NAME,
        external_id=str(item["jobId"]),
        title=(item.get("jobTitle") or "").strip(),
        company=(item.get("employerName") or "").strip(),
        location=(item.get("locationName") or "").strip(),
        salary_min=item.get("minimumSalary") or None,
        salary_max=item.get("maximumSalary") or None,
        url=item.get("jobUrl") or f"https://www.reed.co.uk/jobs/{item['jobId']}",
        posted=parse_uk_date(item.get("date")),
        expires=parse_uk_date(item.get("expirationDate")),
        description=strip_html(item.get("jobDescription") or ""),
        employer_type=_employer_type(item.get("employerName") or ""),
    )


def _search(ctx: SourceContext, **params) -> list[dict]:
    base = {"resultsToTake": 100, "resultsToSkip": 0}
    base.update({k: v for k, v in params.items() if v is not None})
    out: list[dict] = []
    while True:
        r = ctx.get(f"{BASE}/search", params=base, auth=_auth())
        data = r.json()
        results = data.get("results", [])
        out.extend(results)
        total = data.get("totalResults", 0)
        base["resultsToSkip"] += len(results)
        if not results or base["resultsToSkip"] >= min(total, 300):
            break
    return out


def fetch(settings: dict, ctx: SourceContext) -> list[Job]:
    if not ENV.reed_key:
        ctx.errors.append("reed: REED_KEY missing")
        return []
    seen: dict[str, Job] = {}
    radius = settings["radius_miles"]
    part_time_mode = settings["mode"] == "part_time"

    for kw in settings["keywords"]:
        passes = []
        if part_time_mode:
            passes.append({"keywords": kw, "locationName": "Bristol", "distanceFromLocation": radius, "partTime": "true"})
            passes.append({"keywords": kw, "locationName": "Bristol", "distanceFromLocation": radius, "temp": "true"})
            if settings["include_remote"]:
                passes.append({"keywords": f"{kw} remote", "partTime": "true"})
        else:
            passes.append({"keywords": kw, "locationName": "Bristol", "distanceFromLocation": radius})
            if settings["include_remote"]:
                passes.append({"keywords": f"{kw} remote"})
        for p in passes:
            try:
                for item in _search(ctx, **p):
                    job = _to_job(item)
                    seen.setdefault(job.job_id, job)
            except httpx.HTTPError as e:
                ctx.errors.append(f"reed {p}: {safe_err(e)}")
    return list(seen.values())


def enrich(job: Job, ctx: SourceContext) -> Job:
    """Fetch the job detail for explicit flags and the full description."""
    try:
        d = ctx.get(f"{BASE}/jobs/{job.external_id}", auth=_auth()).json()
    except (httpx.HTTPError, ValueError) as e:
        ctx.errors.append(f"reed detail {job.external_id}: {safe_err(e)}")
        return job
    if not d or "jobId" not in d:
        return job
    job.description = strip_html(d.get("jobDescription") or job.description)
    job.detailed = True
    if d.get("partTime"):
        job.contract_time = "part_time"
    elif d.get("fullTime"):
        job.contract_time = "full_time"
    ct = (d.get("contractType") or "").lower()
    if ct.startswith("perm"):
        job.contract_type = "permanent"
    elif ct.startswith("temp"):
        job.contract_type = "temp"
    elif ct.startswith("contract"):
        job.contract_type = "contract"
    job.salary_period = _salary_period(d.get("salaryType"))
    if d.get("minimumSalary"):
        job.salary_min = d["minimumSalary"]
    if d.get("maximumSalary"):
        job.salary_max = d["maximumSalary"]
    if d.get("locationName"):
        job.location = d["locationName"]
    if d.get("externalUrl") and d["externalUrl"].startswith("http"):
        job.tag("direct-apply")
    return job
