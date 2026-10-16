"""One run: settings → fetch → normalise → filter → dedupe → enrich → score → publish."""
from __future__ import annotations

import json
import time
from collections import Counter
from datetime import datetime, timezone

from .config import LOCAL_DIR, PORTAL_DATA, coerce_settings
from .dedupe import dedupe
from .models import Job, enrich_from_text
from .rules import evaluate, heuristic_score, heuristic_why
from .score import score_jobs
from .sheet_api import SheetAPI, SheetError
from .sources import make_context, registry

MAX_DROPPED_ROWS = 300
MAX_ENRICH = 80          # Reed detail calls per run


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")


def job_row(job: Job) -> dict:
    """Shape a Job for the Sheet's Jobs tab (keys = header names)."""
    mode = {"remote": "Remote", "hybrid": "Hybrid", "onsite": "On-site", "unknown": ""}[job.remote]
    if "hybrid-bristol" in job.tags:
        mode = "Hybrid (Bristol)"
    return {
        "job_id": job.job_id,
        "Score": job.score if job.score is not None else "",
        "Title": job.title,
        "Company": job.company,
        "Location": job.location,
        "Work mode": mode,
        "Hours / contract": job.hours_text(),
        "Pay": job.pay_text(),
        "Why it fits": job.why,
        "Red flags": "; ".join(job.red_flags),
        "Apply": job.url,
        "Posted": job.posted.isoformat() if job.posted else "",
        "Source": job.source + (" · " + job.employer_type if job.employer_type != "unknown" else ""),
    }


def dropped_row(job: Job) -> dict:
    return {
        "Title": job.title, "Company": job.company, "Location": job.location[:60],
        "Source": job.source, "Reason": job.drop_reason or "", "Link": job.url,
        "Posted": job.posted.isoformat() if job.posted else "",
    }


def run(*, dry_run: bool = False, only_source: str | None = None, verbose: bool = True, use_ai: bool = True) -> dict:
    t0 = time.time()
    api: SheetAPI | None = None
    raw_settings: dict = {}
    known: dict[str, dict] = {}

    if not dry_run:
        try:
            api = SheetAPI()
            raw_settings = api.settings()
            known = api.known_jobs()
        except SheetError as e:
            if verbose:
                print(f"! Sheet unavailable ({e}); continuing as dry run")
            api = None
    settings = coerce_settings(raw_settings)

    sources = registry()
    enabled = [only_source] if only_source else settings["sources_enabled"]
    ctx = make_context()
    fetched: list[Job] = []
    per_source: Counter = Counter()
    for name in enabled:
        mod = sources.get(name)
        if not mod:
            ctx.errors.append(f"unknown source {name}")
            continue
        before = ctx.calls
        jobs = mod.fetch(settings, ctx)
        per_source[name] = len(jobs)
        fetched.extend(jobs)
        if verbose:
            print(f"  {name}: {len(jobs)} jobs in {ctx.calls - before} calls")

    for job in fetched:
        enrich_from_text(job)
        evaluate(job, settings)

    kept = [j for j in fetched if not j.drop_reason]
    kept, collapsed = dedupe(kept, known)

    # Second look with full descriptions for new Reed jobs (explicit hours/contract flags).
    reed = sources.get("reed")
    enriched = 0
    for job in kept:
        if job.source == "reed" and job.job_id not in known and enriched < MAX_ENRICH:
            reed.enrich(job, ctx)
            enriched += 1
            enrich_from_text(job)
            evaluate(job, settings)
    dropped = [j for j in fetched if j.drop_reason]
    kept = [j for j in kept if not j.drop_reason]

    for job in kept:
        job.score = heuristic_score(job)
        job.why = heuristic_why(job)
        if "hours-unverified" in job.tags:
            job.red_flags.append("hours not stated – must be ≤20/week in term")
        if "vacation-only" in job.tags:
            job.red_flags.append("full-time: only inside a UWE vacation window")
        if "pay-estimated" in job.tags:
            job.red_flags.append("pay is Adzuna's estimate")

    # AI scoring for jobs the Sheet hasn't scored yet; rows already scored keep their values.
    new_ids = [j.job_id for j in kept if j.job_id not in known]
    to_score = [j for j in kept if j.job_id not in known or not str(known[j.job_id].get("score", "")).strip()]
    gemini_calls = 0
    if use_ai and to_score:
        profile = api.profile() if api else None
        gemini_calls = score_jobs(to_score, profile, ctx.errors)
        if verbose:
            print(f"  gemini: scored {sum('ai-scored' in j.tags for j in to_score)}/{len(to_score)} in {gemini_calls} calls")
    for j in kept:
        prev = known.get(j.job_id)
        if prev and j not in to_score and str(prev.get("score", "")).strip():
            j.score = int(float(prev["score"]))
            j.why = prev.get("why") or j.why
            if prev.get("red_flags"):
                j.red_flags = [f for f in str(prev["red_flags"]).split("; ") if f]

    kept.sort(key=lambda j: (-(j.score or 0), j.posted or datetime.min.date()), reverse=False)
    duration = round(time.time() - t0, 1)

    summary = {
        "last_run": _now(),
        "mode": settings["mode"],
        "sources": dict(per_source),
        "fetched": len(fetched),
        "kept": len(kept),
        "new": len(new_ids),
        "dropped": len(dropped),
        "collapsed_duplicates": collapsed,
        "api_calls": ctx.calls,
        "gemini_calls": gemini_calls,
        "errors": ctx.errors,
        "duration_s": duration,
        "drop_reasons": Counter(j.drop_reason.split(" (")[0] for j in dropped).most_common(12),
        "top": [{"title": j.title, "company": j.company, "score": j.score, "pay": j.pay_text(),
                 "hours": j.hours_text(), "url": j.url, "new": j.job_id in new_ids} for j in kept[:15]],
    }

    if api:
        res = api.upsert_jobs([job_row(j) for j in kept])
        summary["sheet"] = res
        api.replace_dropped([dropped_row(j) for j in sorted(dropped, key=lambda j: j.posted or datetime.min.date(), reverse=True)[:MAX_DROPPED_ROWS]])
        api.append_log({
            "Run time": summary["last_run"], "Source": ", ".join(enabled), "Fetched": len(fetched),
            "Kept": len(kept), "New": res.get("new", ""), "Dropped": len(dropped),
            "Errors": "; ".join(ctx.errors)[:500], "Duration s": duration,
            "Notes": f"{ctx.calls} API calls, {gemini_calls} Gemini calls, {collapsed} duplicates collapsed, {enriched} details fetched",
        })
    else:
        LOCAL_DIR.mkdir(exist_ok=True)
        (LOCAL_DIR / "kept.json").write_text(json.dumps([job_row(j) for j in kept], indent=1, default=str))
        (LOCAL_DIR / "dropped.json").write_text(json.dumps([dropped_row(j) for j in dropped], indent=1, default=str))

    PORTAL_DATA.mkdir(parents=True, exist_ok=True)
    (PORTAL_DATA / "summary.json").write_text(json.dumps(summary, indent=1, default=str))

    if verbose:
        print(f"\nfetched {len(fetched)} · kept {len(kept)} ({len(new_ids)} new) · dropped {len(dropped)} · "
              f"{collapsed} dups · {ctx.calls} calls · {duration}s")
        for reason, n in summary["drop_reasons"]:
            print(f"   - {n:3d}  {reason}")
        if ctx.errors:
            print("errors:", *ctx.errors, sep="\n   ")
    return summary
