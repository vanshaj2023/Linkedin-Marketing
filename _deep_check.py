"""Deep checks: things the smoke harness can't catch by import alone."""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

issues = []


def report(severity, location, title, detail=""):
    issues.append({"severity": severity, "where": location, "title": title, "detail": detail})
    safe_detail = detail.encode("ascii", "replace").decode("ascii")
    safe_title = title.encode("ascii", "replace").decode("ascii")
    print(f"[{severity}] {location}: {safe_title}\n         {safe_detail}")


# 1. run_scraper.py: top-level asyncio.run without guard — side effect on import
src = (ROOT / "run_scraper.py").read_text()
tree = ast.parse(src)
has_guard = any(
    isinstance(n, ast.If) and isinstance(n.test, ast.Compare) and
    isinstance(n.test.left, ast.Name) and n.test.left.id == "__name__"
    for n in tree.body
)
if not has_guard:
    report("BLOCKER", "run_scraper.py:39",
           "asyncio.run(main()) runs on import",
           "Importing run_scraper triggers a full LinkedIn scrape because the call "
           "sits at module top level with no `if __name__ == '__main__':` guard. "
           "Any tool that auto-imports modules (linters, tests) launches Playwright "
           "and scrapes the feed.")

# 2. run_scraper.py: Unicode arrow → crashes on Windows console (cp1252)
if "→" in src:
    report("HIGH", "run_scraper.py:36",
           "Unicode arrow `→` triggers UnicodeEncodeError on Windows cp1252 console",
           "`print(f'Saved {len(results)} results → {out_path}')` raises "
           "UnicodeEncodeError when the default code page can't encode U+2192. "
           "Replace with `->` or wrap stdout with utf-8.")

# 3. github_trigger.py: `requests` not declared in requirements.txt
req = (ROOT / "requirements.txt").read_text().lower()
if "requests" not in req:
    src = (ROOT / "github_trigger.py").read_text()
    if "import requests" in src:
        report("HIGH", "github_trigger.py:7 / requirements.txt",
               "github_trigger.py imports `requests` but it isn't in requirements.txt",
               "Either add `requests>=2.31` to requirements.txt or migrate to `httpx` "
               "(already pulled in by fastapi).")

# 4. agents/referral.py: empty to_email recipient
src = (ROOT / "agents" / "referral.py").read_text()
if 'send_referral_email, "", name, company, target_role' in src:
    report("BLOCKER", "agents/referral.py:135",
           "`send_referral_email` called with empty string for `to_email`",
           "step.run(\"send-email\", send_referral_email, \"\", name, company, target_role) — "
           "to_email is always \"\". In DRY_RUN it silently prints success; in production "
           "the SMTP call goes to an empty address and raises (or sends to nobody). The "
           "campaign target's email is never resolved/stored anywhere.")

# 5. mailer/email.py: module name shadows stdlib `email`
src = (ROOT / "mailer" / "email.py").read_text()
if "from email.mime.text import MIMEText" in src:
    report("MEDIUM", "mailer/email.py",
           "Package `mailer.email` shadows stdlib `email`",
           "The file does `from email.mime.text import MIMEText`, which works because "
           "absolute imports take precedence — but anywhere a relative resolver kicks in "
           "(some test runners, deps using __import__('email')) you'd get the local file "
           "instead of stdlib email. Rename to `mailer/mailer.py` or `mailer/smtp.py`.")

# 6. datetime.utcnow() deprecated since Python 3.12 — used everywhere
deprecated_files = []
for path in ROOT.rglob("*.py"):
    if any(p in path.parts for p in ("venv", "__pycache__", ".claude", "output", "_diagnostic_runner.py", "_deep_check.py")):
        continue
    try:
        text = path.read_text(encoding="utf-8")
        if "datetime.utcnow()" in text:
            deprecated_files.append(path.relative_to(ROOT).as_posix())
    except Exception:
        pass
if deprecated_files:
    report("MEDIUM", "many files",
           "`datetime.utcnow()` deprecated in Python 3.12+; removed in 3.14",
           f"Files: {deprecated_files[:10]}{'…' if len(deprecated_files) > 10 else ''}. "
           "Replace with `datetime.datetime.now(datetime.timezone.utc)` (or use "
           "`datetime.UTC` on 3.11+).")

# 7. config.py: APPLICANT_PROFILE — `int(os.getenv("APPLICANT_YOE", "2"))`
# If env var is set but empty string, int("") raises ValueError
src = (ROOT / "config.py").read_text()
if 'int(os.getenv("APPLICANT_YOE", "2"))' in src:
    report("LOW", "config.py:61",
           "int(os.getenv('APPLICANT_YOE','2')) raises on empty string env var",
           "If APPLICANT_YOE='' is in the environment (common Docker/CI footgun) the "
           "default never kicks in and int('') raises ValueError at module import time. "
           "Same risk on WARMUP_WEEK, AUTO_REPOST_THRESHOLD, CONNECTION_THRESHOLD, "
           "JOB_SLACK_THRESHOLD.")

# 8. scrapers/feed.py: dead code import_re = __import__('re') when re is already imported
src = (ROOT / "scrapers" / "feed.py").read_text()
if "import_re = __import__('re')" in src:
    report("LOW", "scrapers/feed.py:199",
           "Inner shadow import of `re` via __import__('re')",
           "Module already does `import re` at line 1. The local `import_re = "
           "__import__('re')` inside `_extract_content` is dead/redundant — drop it.")

# 9. db/__init__.py: created Mongo client at import time
src = (ROOT / "db" / "__init__.py").read_text()
if "client = AsyncIOMotorClient(" in src:
    report("INFO", "db/__init__.py:9",
           "AsyncIOMotorClient instantiated at import time",
           "Doesn't fail at import (it's lazy until first I/O), but means tools that "
           "import the package eagerly will hold an open client handle that they never "
           "close. Consider lazy-construction inside a `get_db()` helper.")

# 10. db/__init__.py: schemas use datetime.utcnow as default_factory (deprecated)
# Same deprecation issue, but separately surfaced — and Pydantic warns in some versions.
# Already covered above.

# 11. browser/easy_apply.py: `modal if True else None` is just `modal`
src = (ROOT / "browser" / "easy_apply.py").read_text()
if "_dismiss_modal(page, modal if True else None)" in src:
    report("LOW", "browser/easy_apply.py:155",
           "`modal if True else None` is dead-code conditional",
           "The expression always evaluates to `modal`. Simplify to "
           "`await _dismiss_modal(page, modal)` or remove the `True` placeholder.")

# 12. browser/easy_apply.py: modal.page may not exist on Locator on older Playwright
# In Playwright 1.44 (Python), Locator does NOT have a `.page` attribute — that came later.
src = (ROOT / "browser" / "easy_apply.py").read_text()
if "modal.page.wait_for_timeout" in src:
    report("HIGH", "browser/easy_apply.py:173",
           "`modal.page` access on Playwright 1.44 — Locator has no `.page` attribute",
           "In `_handle_resume`, `modal` is a `Locator`. Playwright Python only added the "
           "`Locator.page` property in 1.46+; requirements pin to 1.44.*. This raises "
           "AttributeError the first time a resume upload runs. Pass `page` in instead.")

# 13. main.py /dryrun: `import config as cfg_module` inside handler — runtime cost OK,
# but mutating Config.DRY_RUN doesn't help the already-imported `config` symbol in other
# modules that did `from config import config`. They DO share the instance, so OK.
src = (ROOT / "main.py").read_text()

# 14. agents/feed_scout.py: `step.run("scrape-feed", lambda: scrape_organic_feed(...))`
# Inngest can call lambdas but the function is captured by closure — fine in 0.4.
# Bigger issue: scrape_organic_feed makes external requests in DOM order, but step.run
# fully re-runs on retry. With `enrich_urns=True` (default), each retry sends fresh
# "..." menu clicks, which LinkedIn flags. Not a code bug, but a behaviour concern.
# Surface as INFO.

# 15. agents/content.py: `await send_alert(...)` outside step.run — Inngest best practice
# Already surfaced via static test.

# 16. core/circuit_breaker.py: `auto_resume_hours: int = None` — type hint conflict
src = (ROOT / "core" / "circuit_breaker.py").read_text()
if "auto_resume_hours: int = None" in src:
    report("LOW", "core/circuit_breaker.py:21",
           "Type hint `auto_resume_hours: int = None` mismatches default",
           "Should be `Optional[int] = None` or `int | None = None` to satisfy strict "
           "type checkers (mypy / pyright).")

# 17. scrapers/feed.py — `__name__ == \"__main__\"` block runs scrape_hiring_posts(...)
# this is intentional and guarded.

# 18. login.py uses `✓` and emoji warning characters — same Windows console concern
src = (ROOT / "login.py").read_text()
if "✓" in src or "⚠" in src:
    report("MEDIUM", "login.py (multiple)",
           "Non-ASCII output chars (✓, ⚠) crash on Windows cp1252 console",
           "Running `python login.py` on Windows without `PYTHONIOENCODING=utf-8` raises "
           "UnicodeEncodeError when these are printed.")

# 19. config.py: requires_sponsorship/willing_to_relocate stored as strings, not bools
src = (ROOT / "config.py").read_text()
if '"willing_to_relocate": os.getenv("APPLICANT_RELOCATE", "false")' in src:
    report("LOW", "config.py:67-68",
           "Boolean profile fields stored as strings",
           "willing_to_relocate / requires_sponsorship are kept as the raw env string "
           "(e.g. 'false'). Downstream llm.service.answer_application_question feeds them "
           "into a prompt as-is; that works, but you'll trip if anything compares "
           "`profile['requires_sponsorship'] is True`.")

# 20. browser/easy_apply.py:_walk_modal — when modal becomes none mid-loop, code calls
# `modal.locator(...)` after `if await modal.count() == 0:` only by re-evaluating.
# Actually re-fetches every iteration. OK.

# 21. browser/manager.py: get_authenticated_context returns BrowserContext.
# action_queue._dispatch_action calls `context.browser.close()` — BrowserContext.browser
# is a *property* that may be None for non-launched contexts (e.g. when using launch_persistent_context).
# In current code, browser is always launched, so OK.

# 22. main.py: signature for slack_actions writes back 200 OK without setting media_type. OK.

# 23. main.py: /dryrun command mutates Config.DRY_RUN on the SINGLETON instance — OK at runtime.

# 24. scrapers/jobs.py: `await page.goto(url)` without `wait_until` — uses default `load`,
# fine, but the next selector `.jobs-search-results-list` no longer exists in the new
# LinkedIn jobs layout. The list lives under `.scaffold-layout__list-container ul`.
# This makes scrape_jobs frequently return [].
src = (ROOT / "scrapers" / "jobs.py").read_text()
if ".jobs-search-results-list" in src and "scaffold-layout__list-container" not in src:
    report("HIGH", "scrapers/jobs.py:25",
           ".jobs-search-results-list selector is stale on new LinkedIn layout",
           "The 2025/2026 jobs page uses `.scaffold-layout__list-container ul` "
           "instead. As coded, `await page.locator('.jobs-search-results-list').click()` "
           "throws and the next 3 PageDown presses never fire, so jobs are not scrolled "
           "into view. scrape_jobs typically returns the first ~6 cards only.")

# 25. scrapers/people.py: `li.reusable-search__result-container` is the OLD layout
src = (ROOT / "scrapers" / "people.py").read_text()
if "li.reusable-search__result-container" in src:
    report("HIGH", "scrapers/people.py:25",
           "`li.reusable-search__result-container` selector is stale",
           "New LinkedIn people search uses `div[data-chameleon-result-urn]` "
           "(or the generic `li[componentkey]`). This selector returns 0 elements; "
           "`search_people` then returns [].")

# 26. agents/referral.py: `score_connection_profile` is sync; called in async fn — OK,
# but blocks event loop on every call (an LLM HTTP request inside an event loop).
# Each call to Groq is several hundred ms — blocking. Same for generate_connection_note.
report("MEDIUM", "agents/referral.py, agents/job_hunter.py, agents/connection.py",
       "Synchronous LLM/Groq calls run inside async coroutines → blocks event loop",
       "score_connection_profile / generate_connection_note / score_job_post / "
       "score_post_for_repost / classify_feed_post_as_job / generate_engage_comment are "
       "all sync `def`s. Each call to Groq's REST API blocks the asyncio loop for the "
       "request's duration (~200-800ms). For large fan-outs this serializes work and "
       "stalls anything else (e.g. concurrent scrapes). Wrap with "
       "`await asyncio.to_thread(...)` or use the async Groq client.")

# 27. core/action_queue.py: `from inngest_client import inngest_client` happens at the
# bottom AFTER the BUDGET_MAP and ActionQueue class — but the file ALSO imports `from db
# import db, ActionQueueItem` at the top, which loads db.__init__ which constructs Motor
# client. If MongoDB env is unset, that's fine until first operation.
# OK.

# 28. agents/auto_apply.py & job_hunter.py — both use `step.run("scrape-...", lambda: ...)`
# Same lambda issue.

# 29. browser/interactions.py _find_topcard_button — uses page.evaluate with f"-string"
# template. The JS is not f-string interpolated (uses ({kind})). OK because JS gets {kind}
# from the arg, not Python interpolation.
# (line 41) `_FIND_TOPCARD_BUTTON_JS = r"""..."""` raw string, fine.

# 30. Connection.payload's `target_profile_url` key in action_queue dispatch — agents send it,
# dispatcher reads it. Consistent.

# 31. browser/interactions.py: `re.match(r"Invite\s+(.+?)\s+to connect", aria, re.IGNORECASE)`
# at line 494 — only matches when the aria-label starts with "Invite". LinkedIn now also uses
# "Connect with {name}". Should add that. (minor)

# 32. agents/content.py: `engage_list.find({}).to_list(length=100)` — Motor 3.6 API
# supports `to_list(length=...)`. OK.

# 33. main.py slack_commands: `if abs(time.time() - float(timestamp)) > 300:` —
# if timestamp is "" then float("") raises before abs(); the try catches it. OK.

# 34. main.py slack_actions: returns Response(status_code=200) but body is empty bytes.
# Fine for Slack.

# 35. login.py: `await asyncio.to_thread(input, ...)` — input() returns str, OK.

# 36. Tests in repo root (`test_connect_manual.py`, `test_feed_scout.py`, `test_recent_jobs.py`)
# all run `asyncio.run(main())` at module level — same import-time side effect as run_scraper.
# However pytest discovers them inside `tests/` (per pytest.ini testpaths = tests), so
# they should not run.
for fname in ("test_connect_manual.py", "test_feed_scout.py", "test_recent_jobs.py"):
    fp = ROOT / fname
    txt = fp.read_text()
    tree = ast.parse(txt)
    has_guard = any(
        isinstance(n, ast.If) and isinstance(n.test, ast.Compare) and
        isinstance(n.test.left, ast.Name) and n.test.left.id == "__name__"
        for n in tree.body
    )
    if not has_guard and "asyncio.run(main())" in txt:
        report("HIGH", f"{fname}:final",
               "Top-level `asyncio.run(main())` with no __main__ guard",
               f"Importing {fname} (which any test discovery / static analyzer will do) "
               "triggers a real LinkedIn session. Wrap in "
               "`if __name__ == '__main__': asyncio.run(main())`.")

# 37. scrapers/feed.py: `_extract_content` returns "" on failure -> _extract_post_data
# skips post. OK.

# 38. browser/manager.py: `headless: bool = True` arg is OVERRIDDEN at line 50 to
# `"headless": False,` because we use --headless=new args instead. This is intentional
# (and commented).

# 39. browser/manager.py: `launch_options["proxy"] = {"server": proxy_url}` is OK but
# `launch_options.proxy` doesn't have `username`/`password`. If PROXY_URL embeds creds
# they need to be split out. Minor.

# 40. Inngest fan-out shape:
# agents/connection.py: lots of LLM + DB calls inside an Inngest function body, NOT
# inside `step.run`. Means a retry re-executes the whole loop and may duplicate connection
# requests. Surface as a behavior concern.
report("MEDIUM", "agents/connection.py:34-91",
       "Per-profile work isn't wrapped in `step.run` — Inngest retries duplicate side effects",
       "The for-loop calls `db.connections.update_one(... upsert=True)` and "
       "`ActionQueue.push(...)` directly. If the function fails after queuing some but "
       "not all profiles, Inngest retries from the top — those already-queued profiles "
       "will be queued AGAIN (upsert skips DB dup, but ActionQueue has no idempotency "
       "key). Move each profile's mutations under `step.run('process-' + url, ...)`.")

# 41. agents/job_hunter.py: same pattern — db.jobs.insert_one + ActionQueue.push +
# inngest.send all happen outside step.run.

# 42. agents/auto_apply.py: same — db.recent_jobs.insert_one outside step.run.

# 43. config.py: GROQ_API_KEY default "" — then llm/service.py constructs Groq(api_key="").
# Groq SDK accepts empty key and only fails at request time. So no import error, but
# every LLM call raises 401. Note this for production setup.

# 44. browser/manager.py: storage_state path "state.json" is relative. If running from a
# different cwd (e.g. tests/), the file isn't found. The runner.py / main.py use it from
# repo root, but anybody running from elsewhere will get fresh sessions every time.

# 45. browser/easy_apply.py _handle_resume: silently returns when file doesn't exist.
# That leaves the resume upload step incomplete — Easy Apply may then refuse to submit.
# Better to mark needs_human.
src = (ROOT / "browser" / "easy_apply.py").read_text()
if "if not resume_path or not os.path.exists(resume_path):" in src and "return" in src.split("if not resume_path or not os.path.exists(resume_path):")[1].split("\n")[1]:
    report("MEDIUM", "browser/easy_apply.py:168-170",
           "Resume upload step silently returns when file missing",
           "If APPLICANT_RESUME_PATH points at a non-existent file, `_handle_resume` "
           "no-ops. The Easy Apply walker then advances to the next step thinking the "
           "upload succeeded, often hitting a backend validation failure that wastes "
           "an application slot. Either raise a needs_human signal here or pre-validate "
           "the resume on startup.")

# 46. inngest_client.py: inngest_client created with is_production based on SIGNING_KEY
# presence. Means without a signing key the client is non-prod, OK.

# 47. Pydantic v2 `default_factory=datetime.utcnow` — works but Pydantic warns when
# a deprecated callable is used. Note in the broader deprecation issue above.

# 48. agents/auto_apply.py: `MANUAL_APPLY_MIN_SCORE = 65` and `EASY_APPLY_MIN_SCORE = 75`
# are module constants — easy to change but not env-tunable. Minor.

# 49. mailer/email.py: send_referral_email is sync; agents/referral.py wraps it in step.run.
# Sync funcs work in step.run, but the return value (`True`) gets stored verbatim.
# The bigger issue is the `to_email=""` bug above.

# 50. browser/interactions.py: scrolls + clicks Connect button — well-defended in code,
# but the vision fallback charges OpenAI on every miss. With a bad selector list (which
# scrapers/people.py currently has), the connect agent never gets the right profile and
# hammers OpenAI on every connect attempt.

# write out
out = ROOT / "_deep_check_results.json"
import json
out.write_text(json.dumps(issues, indent=2))
print(f"\nFound {len(issues)} issues. Saved to {out}.")
