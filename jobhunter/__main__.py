"""CLI:  python -m jobhunter <command>

  run [--dry-run] [--source NAME]   full pipeline (dry-run never touches the Sheet)
  fetch --source NAME [--limit N]   fetch + filter one source and print a table
  setup                             create/refresh the Sheet tabs via the Apps Script
  ping                              check the Apps Script is reachable
  apps-script                       write local/Code.gs with the secret filled in (paste into the Sheet)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import ENV, LOCAL_DIR, ROOT, coerce_settings


def cmd_run(a):
    from .pipeline import run
    run(dry_run=a.dry_run, only_source=a.source)


def cmd_fetch(a):
    from .models import enrich_from_text
    from .rules import evaluate, heuristic_score
    from .sources import make_context, registry

    mod = registry()[a.source]
    settings = coerce_settings({})
    ctx = make_context()
    jobs = mod.fetch(settings, ctx)
    for j in jobs:
        enrich_from_text(j)
        evaluate(j, settings)
        if not j.drop_reason:
            j.score = heuristic_score(j)
    kept = [j for j in jobs if not j.drop_reason]
    print(f"{a.source}: {len(jobs)} fetched, {len(kept)} kept, {ctx.calls} calls")
    for j in sorted(kept, key=lambda j: -(j.score or 0))[: a.limit]:
        print(f"  [{j.score:3d}] {j.title[:45]:45} | {j.company[:24]:24} | {j.location[:22]:22} | "
              f"{j.hours_text()[:28]:28} | {j.pay_text()}")
    if a.show_dropped:
        print("\ndropped:")
        for j in [j for j in jobs if j.drop_reason][: a.limit]:
            print(f"  {j.title[:45]:45} | {j.location[:22]:22} | {j.drop_reason}")
    if ctx.errors:
        print("errors:", *ctx.errors, sep="\n  ")


def cmd_setup(a):
    from .sheet_api import SheetAPI
    print(SheetAPI().setup())


def cmd_ping(a):
    from .sheet_api import SheetAPI
    print(SheetAPI().ping())


def cmd_apps_script(a):
    src = (ROOT / "apps_script" / "Code.gs").read_text()
    if not ENV.sheet_secret:
        sys.exit("SHEET_SECRET missing from .env")
    LOCAL_DIR.mkdir(exist_ok=True)
    out = LOCAL_DIR / "Code.gs"
    out.write_text(src.replace("__SHEET_SECRET__", ENV.sheet_secret)
                      .replace("__GITHUB_REPO__", f"{ENV.github_user}/uk-job-hunter"))
    print(f"written {out}  (contains your secret – do not commit; paste into Extensions → Apps Script)")


def main(argv=None):
    p = argparse.ArgumentParser(prog="jobhunter", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run"); r.add_argument("--dry-run", action="store_true"); r.add_argument("--source"); r.set_defaults(f=cmd_run)
    f = sub.add_parser("fetch"); f.add_argument("--source", required=True); f.add_argument("--limit", type=int, default=25)
    f.add_argument("--show-dropped", action="store_true"); f.set_defaults(f=cmd_fetch)
    sub.add_parser("setup").set_defaults(f=cmd_setup)
    sub.add_parser("ping").set_defaults(f=cmd_ping)
    sub.add_parser("apps-script").set_defaults(f=cmd_apps_script)

    a = p.parse_args(argv)
    a.f(a)


if __name__ == "__main__":
    main()
