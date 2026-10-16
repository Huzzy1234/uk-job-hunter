"""Job sources. Each module exposes `NAME` and `fetch(settings, ctx) -> list[Job]`.

`ctx` is a SourceContext: shared HTTP client, call counter and error list, so the
pipeline can log per-source counts and keep inside free-tier quotas.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx

from ..models import Job

USER_AGENT = "uk-job-hunter/0.1 (personal job search tool; contact via GitHub Huzzy1234)"


@dataclass
class SourceContext:
    client: httpx.Client
    min_interval: float = 1.0
    calls: int = 0
    errors: list[str] = field(default_factory=list)
    _last: float = 0.0

    def get(self, url: str, **kwargs) -> httpx.Response:
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        self.calls += 1
        r = self.client.get(url, **kwargs)
        r.raise_for_status()
        return r


def make_context(min_interval: float = 1.0) -> SourceContext:
    client = httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    return SourceContext(client=client, min_interval=min_interval)


def registry() -> dict[str, object]:
    from . import adzuna, reed

    return {m.NAME: m for m in (reed, adzuna)}


__all__ = ["Job", "SourceContext", "make_context", "registry"]
