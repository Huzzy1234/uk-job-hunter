"""Repo housekeeping through the GitHub API (needs GITHUB_TOKEN in .env).

  python scripts/github_admin.py secrets           push the pipeline secrets from .env
  python scripts/github_admin.py dispatch          start the job-search workflow now
  python scripts/github_admin.py runs              list the last few workflow runs
  python scripts/github_admin.py pages             enable GitHub Pages (Actions source)
"""
from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv
from nacl import encoding, public

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
TOKEN = os.environ["GITHUB_TOKEN"]
REPO = f"{os.environ.get('GITHUB_USER', 'Huzzy1234')}/uk-job-hunter"
API = "https://api.github.com"
H = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}

SECRET_NAMES = ["REED_KEY", "ADZUNA_ID", "ADZUNA_KEY", "GEMINI_KEY", "SHEET_SECRET", "APPS_SCRIPT_URL"]


def _encrypt(public_key: str, value: str) -> str:
    pk = public.PublicKey(public_key.encode(), encoding.Base64Encoder())
    return base64.b64encode(public.SealedBox(pk).encrypt(value.encode())).decode()


def secrets():
    key = httpx.get(f"{API}/repos/{REPO}/actions/secrets/public-key", headers=H).raise_for_status().json()
    for name in SECRET_NAMES:
        value = os.environ.get(name, "")
        if not value:
            print(f"  skip {name} (empty in .env)")
            continue
        r = httpx.put(f"{API}/repos/{REPO}/actions/secrets/{name}", headers=H,
                      json={"encrypted_value": _encrypt(key["key"], value), "key_id": key["key_id"]})
        print(f"  {name}: {'ok' if r.status_code in (201, 204) else r.text[:120]}")


def dispatch():
    r = httpx.post(f"{API}/repos/{REPO}/actions/workflows/run.yml/dispatches", headers=H, json={"ref": "main"})
    print("started" if r.status_code == 204 else f"{r.status_code}: {r.text[:200]}")


def runs():
    data = httpx.get(f"{API}/repos/{REPO}/actions/runs?per_page=5", headers=H).raise_for_status().json()
    for w in data.get("workflow_runs", []):
        print(f"  {w['created_at']}  {w['name']:<12} {w['status']:<12} {w['conclusion'] or '-':<10} {w['html_url']}")


def pages():
    r = httpx.post(f"{API}/repos/{REPO}/pages", headers=H, json={"build_type": "workflow"})
    if r.status_code == 409:
        r = httpx.put(f"{API}/repos/{REPO}/pages", headers=H, json={"build_type": "workflow"})
    print(r.status_code, r.text[:200])
    info = httpx.get(f"{API}/repos/{REPO}/pages", headers=H)
    if info.status_code == 200:
        print("  url:", info.json().get("html_url"))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "runs"
    {"secrets": secrets, "dispatch": dispatch, "runs": runs, "pages": pages}[cmd]()
