"""Collapse the same advert seen through several sources or re-posted by agencies."""
from __future__ import annotations

import re

from rapidfuzz import fuzz

from .models import Job

_NOISE = re.compile(r"\b(ltd|limited|plc|llp|uk|group|recruitment|the)\b|[^a-z0-9 ]", re.I)
SOURCE_PRIORITY = {"reed": 0, "adzuna": 2}   # lower wins when two records are the same ad


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", _NOISE.sub(" ", s.lower())).strip()


def same_ad(a: Job, b: Job) -> bool:
    if fuzz.token_set_ratio(norm(a.title), norm(b.title)) < 92:
        return False
    ca, cb = norm(a.company), norm(b.company)
    if ca and cb and fuzz.token_set_ratio(ca, cb) < 85:
        return False
    la, lb = norm(a.location), norm(b.location)
    if la and lb and fuzz.partial_ratio(la, lb) < 60:
        return False
    return True


def _richness(j: Job) -> tuple:
    return (-SOURCE_PRIORITY.get(j.source, 5), j.detailed, len(j.description), bool(j.salary_min))


def dedupe(jobs: list[Job], known: dict[str, dict] | None = None) -> tuple[list[Job], int]:
    """Return (unique jobs, number collapsed).

    `known` maps job_id -> {title, company} for rows already in the Sheet, so a
    re-post from another source doesn't create a second row.
    """
    kept: list[Job] = []
    collapsed = 0
    for job in sorted(jobs, key=_richness, reverse=True):
        dup = next((k for k in kept if same_ad(job, k)), None)
        if dup:
            collapsed += 1
            continue
        kept.append(job)

    if known:
        known_jobs = [
            Job(source=k.split(":")[0], external_id=k.split(":", 1)[1], title=v.get("title", ""),
                company=v.get("company", ""), url="x", location=v.get("location", ""))
            for k, v in known.items()
        ]
        out = []
        for job in kept:
            if job.job_id in known:
                out.append(job)
                continue
            if any(same_ad(job, k) for k in known_jobs):
                collapsed += 1
                continue
            out.append(job)
        kept = out
    return kept, collapsed
