# UK Job Hunter

Finds part-time, temp and remote finance roles in Bristol/Bath that a Student-visa holder
can legally take (≤20 h/week in term), scores them, and writes them to a Google Sheet.
Runs twice a day on GitHub Actions for free.

## How it works

```
GitHub Actions ──► python -m jobhunter run ──► Apps Script (inside the Sheet) ──► Google Sheet
                   Reed · Adzuna · (more)       doGet / doPost JSON API              Jobs · Dropped · Settings · Log
```

- `jobhunter/sources/` – one module per job source.
- `jobhunter/rules.py` – the legality/location/pay filters (unit-tested).
- `jobhunter/pipeline.py` – one run end to end.
- `apps_script/Code.gs` – the Sheet backend. One file, pasted into the Sheet.
- `portal/` – supervision portal (GitHub Pages), reads the same Sheet API.

## Local use

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # fill in keys
.venv/bin/python -m pytest -q
.venv/bin/python -m jobhunter fetch --source reed --show-dropped
.venv/bin/python -m jobhunter run --dry-run      # writes local/kept.json, never touches the Sheet
.venv/bin/python -m jobhunter run                # writes to the Sheet
```

## Setup (once)

1. **Sheet backend.** `python -m jobhunter apps-script` writes `local/Code.gs` with your secret in it.
   In the Sheet: Extensions → Apps Script → replace everything with that file → Deploy → New deployment →
   Web app → Execute as *Me*, Who has access *Anyone* → Deploy → copy the `/exec` URL into `.env` as `APPS_SCRIPT_URL`.
   Then `python -m jobhunter setup` creates the tabs.
2. **GitHub.** Repo → Settings → Secrets and variables → Actions → add
   `REED_KEY ADZUNA_ID ADZUNA_KEY GEMINI_KEY APPS_SCRIPT_URL SHEET_SECRET` (same values as `.env`).
3. **Run now button** (optional). Create a fine-grained GitHub token (this repo, Actions: read & write) and
   store it in the Sheet: Apps Script → Project settings → Script properties → `GITHUB_TOKEN`.

Changing `Code.gs` later: Apps Script → Deploy → Manage deployments → ✎ → Version: New → Deploy (URL stays the same).

## Legal and fair use

Only sources with an API, feed or explicit permission are used. LinkedIn, Indeed, Totaljobs,
CV-Library and GOV.UK Find a job are linked for manual checking, never scraped.
