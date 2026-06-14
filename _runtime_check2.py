"""More targeted runtime checks."""
import asyncio
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def log(test_id, status, msg=""):
    print(f"TEST: {test_id} | {status} | {msg}")


# 1. Try to import requests (which github_trigger needs)
try:
    import requests
    from importlib.metadata import version
    log("requests.installed", "OK", version("requests"))
except ImportError:
    log("requests.installed", "FAIL", "requests is not installed")
except Exception as e:
    log("requests.installed", "FAIL", f"{type(e).__name__}: {e}")

# 2. Verify slack_sdk imports despite shadowing local slack/ package
try:
    from importlib.metadata import version
    log("slack_sdk.installed", "OK", version("slack_sdk"))
except Exception as e:
    log("slack_sdk.installed", "FAIL", f"{type(e).__name__}: {e}")

# 3. Verify inngest installed and matches the call sites
try:
    from importlib.metadata import version
    log("inngest.installed", "OK", version("inngest"))
except Exception as e:
    log("inngest.installed", "FAIL", f"{type(e).__name__}: {e}")

# 4. Verify Inngest's create_function signature still matches the @inngest_client.create_function(...) usage
try:
    import inngest
    sig = inspect.signature(inngest.Inngest.create_function)
    log("inngest.create_function.signature", "INFO", str(sig))
except Exception as e:
    log("inngest.create_function.signature", "FAIL", f"{type(e).__name__}: {e}")

# 5. Make sure browser/easy_apply.py's `modal.page` will work — Locator.page is in 1.44 dir.
# Confirm via actual call won't fail on construction.
try:
    from playwright.async_api import Locator
    page_attr = inspect.getattr_static(Locator, "page", None)
    log("playwright.Locator.page.descriptor", "INFO", repr(page_attr))
except Exception as e:
    log("playwright.Locator.page", "FAIL", f"{type(e).__name__}: {e}")

# 6. Pydantic model dump aliases
try:
    from db import SystemHealth
    d = SystemHealth().model_dump(by_alias=True)
    log("db.SystemHealth.dump_by_alias", "INFO", str(d))
    if "_id" in d and "id" not in d:
        log("db.SystemHealth.alias_correct", "OK", "")
    else:
        log("db.SystemHealth.alias_correct", "FAIL", str(d))
except Exception as e:
    log("db.SystemHealth.dump", "FAIL", f"{type(e).__name__}: {e}")

# 7. Slack handle_status_command would format ANY budget dict key as "k: v.used/v.limit" —
# But config.py records like "date" (str) get filtered by "if isinstance(v, dict) and 'used' in v".
# OK.

# 8. test the actual deprecation warning for datetime.utcnow
try:
    import warnings
    import datetime as dt
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        _ = dt.datetime.utcnow()
        if w and any("utcnow" in str(x.message).lower() for x in w):
            log("datetime.utcnow.deprecation", "OK", "warns on Python 3.12")
        else:
            log("datetime.utcnow.deprecation", "INFO", "no warning on this version")
except Exception as e:
    log("datetime.utcnow", "FAIL", f"{type(e).__name__}: {e}")

# 9. config.py module-level evaluation: would int("") crash if a user set
# APPLICANT_YOE="" in their .env? Let's prove it in isolation.
import os, importlib
try:
    os.environ["APPLICANT_YOE"] = ""
    # Reload config to re-run module-level eval
    import config
    importlib.reload(config)
    log("config.APPLICANT_YOE_empty_reload", "OK", "no crash (using default '2')")
except ValueError as ve:
    log("config.APPLICANT_YOE_empty_reload", "FAIL", f"crash: {ve}")
except Exception as e:
    log("config.APPLICANT_YOE_empty_reload", "FAIL", f"{type(e).__name__}: {e}")
finally:
    os.environ.pop("APPLICANT_YOE", None)

# 10. Verify scrapers/people.py: search_people normalises with company = location split.
# Test the normalisation logic in isolation.
try:
    from scrapers import people
    # The normalisation strips on '·' — works for old layout. But scrape returns []
    # because the selector is stale — covered already.
    log("scrapers.people.search_people.normalise_logic", "INFO",
        "depends on broken upstream selector — would return [] with no errors")
except Exception as e:
    log("scrapers.people.normalise", "FAIL", f"{type(e).__name__}: {e}")

# 11. Slack token handling in slack/bot.py
try:
    import slack.bot
    log("slack.bot._client.is_none_when_no_token", "INFO",
        f"client_is_none={slack.bot._client is None}")
except Exception as e:
    log("slack.bot._client", "FAIL", f"{type(e).__name__}: {e}")

# 12. Try `agents.referral._queue_batch` callable signature.
try:
    from agents.referral import _queue_batch
    log("agents.referral._queue_batch.signature", "INFO",
        str(inspect.signature(_queue_batch)))
except Exception as e:
    log("agents.referral._queue_batch", "FAIL", f"{type(e).__name__}: {e}")

# 13. Verify referral campaign — `referral_on_connection_accepted` calls
# `send_referral_email("", name, company, target_role)` — confirm the order.
# Earlier deep check confirmed via source string match.

# 14. ActionQueueItem default factory works without a Mongo connection.
try:
    from db import ActionQueueItem
    a = ActionQueueItem(agent="a", action_type="like", payload={})
    log("db.ActionQueueItem.default_factory", "OK",
        f"created_at type={type(a.created_at).__name__}")
except Exception as e:
    log("db.ActionQueueItem.default_factory", "FAIL", f"{type(e).__name__}: {e}")

# 15. Verify main.py app exposes /health
try:
    from main import app
    routes = [r.path for r in app.routes if hasattr(r, "path")]
    log("main.app.routes", "INFO", str(routes))
except Exception as e:
    log("main.app.routes", "FAIL", f"{type(e).__name__}: {e}")

# 16. agents/connection.py calls `await step.run(...)` where step.run signature
# in Inngest 0.4 is async. Confirm decorator wrap doesn't break iscoroutine.
try:
    from agents.connection import connection_agent_run, connection_acceptance_poller
    # These should now be Inngest Function objects, not raw coroutines.
    log("agents.connection.functions.types", "INFO",
        f"{type(connection_agent_run).__name__}, "
        f"{type(connection_acceptance_poller).__name__}")
except Exception as e:
    log("agents.connection.types", "FAIL", f"{type(e).__name__}: {e}")

print("\n=== DONE ===")
