"""Runtime tests: actually invoke pure functions and verify lib behaviours."""
import asyncio
import inspect
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

results = []


def log(test_id, status, msg=""):
    print(f"TEST: {test_id} | {status} | {msg}")
    results.append((test_id, status, msg))


# 1. Verify Playwright Locator does/doesn't have `.page` on installed version
try:
    import playwright
    log("playwright.version", "INFO", playwright.__version__)
    from playwright.async_api import Locator
    has_page_attr = "page" in dir(Locator)
    # Even better: check via signature
    log("playwright.Locator.page_attribute_exists", "INFO", str(has_page_attr))
    # Try to get the actual property
    page_attr = getattr(Locator, "page", None)
    log("playwright.Locator.page_descriptor", "INFO", repr(page_attr))
except Exception as e:
    log("playwright.version", "FAIL", f"{type(e).__name__}: {e}")

# 2. Verify Pydantic shared-default bug claim — re-run the test
try:
    from db import DailyBudgets
    a = DailyBudgets(date="aa")
    b = DailyBudgets(date="bb")
    a.likes.used = 999
    if b.likes.used == 999:
        log("db.DailyBudgets.shared_default", "FAIL",
            "mutating a.likes.used leaks into b.likes.used")
    else:
        log("db.DailyBudgets.shared_default", "OK", f"b={b.likes.used}")
except Exception as e:
    log("db.DailyBudgets.shared_default", "FAIL", f"{type(e).__name__}: {e}")

# 3. Verify Config attribute mutation works as expected
try:
    from config import config
    original = config.DRY_RUN
    config.DRY_RUN = not original
    import config as cm
    if cm.config.DRY_RUN == (not original):
        log("config.singleton_mutation", "OK", "shared")
    else:
        log("config.singleton_mutation", "FAIL", "not shared")
    config.DRY_RUN = original
except Exception as e:
    log("config.singleton_mutation", "FAIL", f"{type(e).__name__}: {e}")

# 4. Verify int(getenv()) crash if env is empty
try:
    os.environ["TEST_EMPTY"] = ""
    try:
        v = int(os.getenv("TEST_EMPTY", "2"))
        log("getenv.empty_string", "OK", f"v={v}")
    except ValueError as ve:
        log("getenv.empty_string", "FAIL", f"int('') raises: {ve}")
    del os.environ["TEST_EMPTY"]
except Exception as e:
    log("getenv.empty_string", "FAIL", f"{type(e).__name__}: {e}")

# 5. Verify Groq client constructable with empty key (does not fail until first call)
try:
    import groq
    try:
        c = groq.Groq(api_key="")
        log("groq.empty_key.construct", "OK", "")
    except Exception as e:
        log("groq.empty_key.construct", "FAIL", f"{type(e).__name__}: {e}")
except Exception as e:
    log("groq.import", "FAIL", f"{type(e).__name__}: {e}")

# 6. Verify Inngest function decorator wrapping
try:
    from agents.connection import connection_agent_run
    # Inngest decorates with @inngest_client.create_function returning a Function object
    log("agents.connection_agent_run.type", "INFO", type(connection_agent_run).__name__)
except Exception as e:
    log("agents.connection_agent_run.type", "FAIL", f"{type(e).__name__}: {e}")

# 7. Check that `slack_sdk.web.async_client` exists (slack package shadow concern)
try:
    from slack_sdk.web.async_client import AsyncWebClient
    log("slack_sdk.async_import_through_local_slack_pkg", "OK", "")
except Exception as e:
    log("slack_sdk.async_import_through_local_slack_pkg", "FAIL",
        f"{type(e).__name__}: {e}")

# 8. Check db setup_indexes signature
try:
    from db import setup_indexes
    if inspect.iscoroutinefunction(setup_indexes):
        log("db.setup_indexes.is_async", "OK", "")
    else:
        log("db.setup_indexes.is_async", "FAIL", "should be async")
except Exception as e:
    log("db.setup_indexes.is_async", "FAIL", f"{type(e).__name__}: {e}")

# 9. Inspect signature of `step.run` callers — agents call step.run("name", fn, *args).
# Inngest 0.4: step.run(step_id, handler, *args) — supported.
try:
    import inngest
    log("inngest.version", "INFO", inngest.__version__)
except Exception as e:
    log("inngest.version", "FAIL", f"{type(e).__name__}: {e}")

# 10. Check apply_warmup_budget accepts int week
try:
    from core.warmup import apply_warmup_budget, WARMUP_SCHEDULE
    sig = inspect.signature(apply_warmup_budget)
    log("warmup.apply_warmup_budget.signature", "INFO", str(sig))
except Exception as e:
    log("warmup.apply_warmup_budget.signature", "FAIL", f"{type(e).__name__}: {e}")

# 11. Check that easy_apply._handle_resume signature is correct & callable
try:
    from browser.easy_apply import _handle_resume, _walk_modal, run_easy_apply
    log("easy_apply.signatures", "INFO",
        f"_handle_resume{inspect.signature(_handle_resume)} "
        f"_walk_modal{inspect.signature(_walk_modal)} "
        f"run_easy_apply{inspect.signature(run_easy_apply)}")
except Exception as e:
    log("easy_apply.signatures", "FAIL", f"{type(e).__name__}: {e}")

# 12. Verify the BUDGET_MAP coverage vs all action_types pushed by agents
import_calls = []
for path in (ROOT / "agents").rglob("*.py"):
    txt = path.read_text(encoding="utf-8")
    # crude grep for ActionQueue.push("...", "...", ...)
    import re
    for m in re.finditer(r"ActionQueue\.push\(\s*['\"]\w+['\"]\s*,\s*['\"](\w+)['\"]\s*,", txt):
        import_calls.append((path.name, m.group(1)))
log("agents.action_types_used", "INFO", str(set(c[1] for c in import_calls)))

from core.action_queue import BUDGET_MAP
unmapped = {c[1] for c in import_calls} - set(BUDGET_MAP.keys())
if unmapped:
    log("budget_map.coverage", "FAIL", f"unmapped: {unmapped}")
else:
    log("budget_map.coverage", "OK", "")

# 13. Verify search_jobs is called as step.run("...", search_jobs, keyword, location, 10).
# But the SIGNATURE of search_jobs is (keywords: str, location: str = "", max_results: int = 20)
# job_hunter passes (keyword, location, 10) - all positional - matches.
try:
    from scrapers.jobs import search_jobs
    sig = inspect.signature(search_jobs)
    log("scrapers.jobs.search_jobs.signature", "INFO", str(sig))
except Exception as e:
    log("scrapers.jobs.search_jobs.signature", "FAIL", f"{type(e).__name__}: {e}")

# 14. Verify search_people signature vs how it's called in connection.py
# call: search_people(keyword, 20). signature: search_people(keywords, max_results=30)
try:
    from scrapers.people import search_people
    sig = inspect.signature(search_people)
    log("scrapers.people.search_people.signature", "INFO", str(sig))
except Exception as e:
    log("scrapers.people.search_people.signature", "FAIL", f"{type(e).__name__}: {e}")

# 15. Verify the LLM service signatures vs callers
try:
    from llm.service import (
        score_job_post, classify_feed_post_as_job, generate_engage_comment,
    )
    log("llm.score_job_post.signature", "INFO", str(inspect.signature(score_job_post)))
    log("llm.classify_feed_post_as_job.signature", "INFO",
        str(inspect.signature(classify_feed_post_as_job)))
except Exception as e:
    log("llm.signatures", "FAIL", f"{type(e).__name__}: {e}")

# 16. Pydantic Connection/Job validation — Connection requires linkedin_url, name,
# headline, company, source_agent. But agents/connection.py upserts with
# {"$setOnInsert": {"linkedin_url": ..., "name": ..., "headline": ..., "company": ...,
# "source_agent": "connection", ...}} — OK.

# 17. Check that EngageListMember's "added_by_agent" is set by referral.py
# referral.py upserts with: added_by_agent=f"referral:{campaign_id}" — OK.

# 18. Verify mail send_referral_email function signature vs how the referral agent
# calls it: step.run("send-email", send_referral_email, "", name, company, target_role)
try:
    from mailer.email import send_referral_email
    sig = inspect.signature(send_referral_email)
    log("mailer.send_referral_email.signature", "INFO", str(sig))
    # (to_email, to_name, company, role) — referral passes "" for to_email. Confirmed bug.
except Exception as e:
    log("mailer.send_referral_email.signature", "FAIL", f"{type(e).__name__}: {e}")

# 19. db.Connection schema has no 'template_used' field but connection_agent_run upserts it.
# Pydantic v2 with extra="ignore" silently drops it; with extra="allow" stores it; default
# is "ignore" but MotorClient bypasses Pydantic entirely (raw dict). So Mongo stores it.
# But the schema docs lie. Note.
try:
    from db import Connection
    fields = Connection.model_fields.keys()
    if "template_used" not in fields:
        log("db.Connection.template_used", "MISMATCH",
            "agents/connection.py writes `template_used` but Connection schema doesn't define it")
except Exception as e:
    log("db.Connection.template_used", "FAIL", f"{type(e).__name__}: {e}")

# 20. SystemHealth uses alias _id; CircuitBreaker.status filters by {"_id": "circuit_breaker"}
# but CircuitBreaker.status() inserts with SystemHealth().model_dump(by_alias=True) — OK.
# But the model returns "_id" key because of alias; when read back via Motor, the dict has
# "_id" key. CircuitBreaker.status reads health["status"] — OK.

# 21. Check db indexes — duplicate on linkedin_url for connections allows global unique;
# but in agents/connection.py the source_agent is just "connection", so good.

# 22. core.action_queue dispatches "view_profile" using get_browser_page; closes
# context.browser and p_instance. OK.

# 23. browser.interactions._find_topcard_button passes `{"kind": kind}` as the `arg`,
# JS receives it as the function's first param. OK.

# 24. agents/feed_scout.py passes `lambda: scrape_organic_feed(max_posts=40)` to step.run.
# step.run accepts a callable returning awaitable. Inngest python 0.4 wraps it. OK.

# 25. Re-confirm Playwright BrowserContext.browser attribute exists
try:
    from playwright.async_api import BrowserContext
    log("playwright.BrowserContext.browser_attr_exists", "INFO",
        str("browser" in dir(BrowserContext)))
except Exception as e:
    log("playwright.BrowserContext.attr", "FAIL", f"{type(e).__name__}: {e}")

print("\n=== DONE ===")
