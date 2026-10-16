from datetime import date, timedelta

import pytest

from jobhunter import rules
from jobhunter.config import coerce_settings
from jobhunter.dedupe import dedupe
from jobhunter.models import Job, detect_hours, enrich_from_text, looks_self_employed

S = coerce_settings({})


def mk(**kw) -> Job:
    base = dict(source="reed", external_id="1", title="Finance Assistant", url="http://x",
                company="Acme Ltd", location="Bristol", posted=date.today())
    base.update(kw)
    return enrich_from_text(Job(**base))


def ev(**kw) -> Job:
    return rules.evaluate(mk(**kw), S)


# ---- hours parsing ---------------------------------------------------------

@pytest.mark.parametrize("text,hours", [
    ("Part-time, 16 hours per week, flexible", 16),
    ("20 hrs pw", 20),
    ("working 22.5 hours a week", 22.5),
    ("3 days a week", 22.5),
    ("two days per week", 15),
    ("0.6 FTE", 22.5),
    ("15-20 hours per week", 20),
    ("no hours mentioned", None),
])
def test_detect_hours(text, hours):
    assert detect_hours(text) == hours


# ---- legal hours ------------------------------------------------------------

def test_part_time_16h_kept():
    j = ev(description="Part-time, 16 hours per week")
    assert j.drop_reason is None and "hours-ok" in j.tags


def test_over_20h_permanent_dropped():
    j = ev(description="25 hours per week, permanent")
    assert "over the 20-hour limit" in j.drop_reason


def test_full_time_permanent_dropped():
    j = ev(description="Full-time permanent position, 37.5 hours")
    assert j.drop_reason and "permanent" in j.drop_reason


def test_freelance_dropped():
    assert looks_self_employed("self-employed bookkeeper, invoice us monthly")
    j = ev(description="Freelance, outside IR35, day rate")
    assert "self-employed" in j.drop_reason


def test_paye_temp_not_self_employed():
    assert not looks_self_employed("temporary worker paid weekly via PAYE through our agency, day rate")


def test_temp_unknown_hours_kept_with_flag():
    j = ev(description="Temporary accounts assistant, immediate start", contract_type="temp")
    assert j.drop_reason is None and "hours-unverified" in j.tags


def test_full_time_temp_outside_vacation(monkeypatch):
    monkeypatch.setattr(rules, "today", lambda: date(2026, 10, 16))
    j = ev(description="Full-time temp for 2 weeks", contract_type="temp", contract_time="full_time")
    assert "vacation window" in j.drop_reason


def test_full_time_temp_near_christmas(monkeypatch):
    monkeypatch.setattr(rules, "today", lambda: date(2026, 12, 1))
    j = ev(description="Full-time temp over Christmas, 2 weeks", contract_type="temp", contract_time="full_time",
           posted=date(2026, 11, 30))
    assert j.drop_reason is None and "vacation-only" in j.tags


def test_bare_remote_word_not_enough():
    j = ev(location="London", description="Part-time 20 hours per week. Occasional remote working after probation.")
    assert j.drop_reason == "London, not remote"


def test_remote_in_title_enough():
    j = ev(title="Remote Finance Assistant", location="London", description="Part-time 20 hours per week.")
    assert j.drop_reason is None and "remote" in j.tags


# ---- location ---------------------------------------------------------------

def test_london_hybrid_dropped():
    j = ev(location="London", description="Hybrid, 2 days in the office, part-time 20 hours per week")
    assert "London hybrid" in j.drop_reason


def test_remote_london_kept():
    j = ev(location="London", description="Fully remote, part-time 20 hours per week")
    assert j.drop_reason is None and "remote" in j.tags


def test_manchester_dropped():
    j = ev(location="Manchester", description="part-time 20 hours per week")
    assert "outside Bristol" in j.drop_reason


def test_bath_kept():
    j = ev(location="Bath, Somerset", description="part-time 15 hours per week")
    assert j.drop_reason is None


def test_latlon_inside_radius():
    j = ev(location="Somewhere", latitude=51.50, longitude=-2.55, description="part-time 15 hours per week")
    assert j.drop_reason is None


# ---- pay --------------------------------------------------------------------

def test_below_minimum_wage_dropped():
    j = ev(description="part-time 16 hours per week", salary_min=11.5, salary_max=12.0, salary_period="hour")
    assert "below" in j.drop_reason


def test_good_hourly_pay_tagged():
    j = ev(description="part-time 16 hours per week", salary_min=16, salary_max=18, salary_period="hour")
    assert j.drop_reason is None and "pay-good" in j.tags


def test_annual_fte_converted():
    j = ev(description="part-time 16 hours per week", salary_min=30000, salary_max=32000, salary_period="year")
    assert j.drop_reason is None and "pay-good" in j.tags and j.pay_text() == "£30,000–£32,000/yr FTE"


# ---- title / freshness --------------------------------------------------------

def test_non_finance_title_dropped():
    assert "not a finance" in ev(title="Warehouse Operative", description="part-time 16 hours").drop_reason


def test_director_excluded():
    assert "excluded" in ev(title="Finance Director", description="part-time 16 hours").drop_reason


def test_stale_dropped():
    j = ev(posted=date.today() - timedelta(days=30), description="part-time 16 hours per week")
    assert "older" in j.drop_reason


# ---- dedupe ---------------------------------------------------------------------

def test_dedupe_same_ad_two_sources():
    a = mk(source="reed", external_id="1", title="Part Time Finance Assistant", company="Acme Ltd")
    b = mk(source="adzuna", external_id="9", title="Part-Time Finance Assistant", company="Acme Limited")
    kept, n = dedupe([a, b])
    assert n == 1 and kept[0].source == "reed"


def test_dedupe_against_known():
    a = mk(source="adzuna", external_id="9", title="Finance Assistant", company="Acme")
    kept, n = dedupe([a], known={"reed:1": {"title": "Finance Assistant", "company": "Acme Ltd", "location": "Bristol"}})
    assert kept == [] and n == 1
