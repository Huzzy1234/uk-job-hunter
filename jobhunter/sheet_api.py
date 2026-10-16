"""Client for the Apps Script web app bound to the Google Sheet.

GET  {url}?action=...&key=SECRET
POST {url}  body = JSON {key, action, ...}  (Content-Type text/plain avoids CORS preflight
                                             in the browser; harmless from Python)
Apps Script answers with a 302 to googleusercontent.com; follow_redirects handles it.
"""
from __future__ import annotations

import json
import time

import httpx

from .config import ENV


class SheetError(RuntimeError):
    pass


class SheetAPI:
    def __init__(self, url: str | None = None, secret: str | None = None):
        self.url = (url or ENV.apps_script_url).strip()
        self.secret = secret or ENV.sheet_secret
        if not self.url:
            raise SheetError("APPS_SCRIPT_URL is not set")
        self.client = httpx.Client(timeout=120, follow_redirects=True)

    def _check(self, r: httpx.Response) -> dict:
        try:
            data = r.json()
        except ValueError:
            raise SheetError(f"non-JSON reply ({r.status_code}): {r.text[:300]}")
        if not data.get("ok"):
            raise SheetError(data.get("error", "unknown error"))
        return data

    def get(self, action: str, **params) -> dict:
        for attempt in range(3):
            try:
                r = self.client.get(self.url, params={"action": action, "key": self.secret, **params})
                return self._check(r)
            except (httpx.HTTPError, SheetError) as e:
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))
        raise SheetError("unreachable")

    def post(self, action: str, **payload) -> dict:
        body = json.dumps({"key": self.secret, "action": action, **payload}, default=str)
        for attempt in range(3):
            try:
                r = self.client.post(self.url, content=body, headers={"Content-Type": "text/plain;charset=utf-8"})
                return self._check(r)
            except (httpx.HTTPError, SheetError) as e:
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))
        raise SheetError("unreachable")

    # convenience wrappers
    def ping(self) -> dict:
        return self.get("ping")

    def setup(self) -> dict:
        return self.post("setup")

    def settings(self) -> dict:
        return self.get("settings").get("settings", {})

    def known_jobs(self) -> dict[str, dict]:
        """job_id -> {status, title, company, location}"""
        rows = self.get("ids").get("rows", [])
        return {r["job_id"]: r for r in rows if r.get("job_id")}

    def upsert_jobs(self, rows: list[dict]) -> dict:
        return self.post("upsert_jobs", jobs=rows)

    def replace_dropped(self, rows: list[dict]) -> dict:
        return self.post("replace_dropped", rows=rows)

    def append_log(self, row: dict) -> dict:
        return self.post("append_log", row=row)
