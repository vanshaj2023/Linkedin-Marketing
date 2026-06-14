# Audit Report — LinkedIn Automation

Date: 2026-06-14
Tooling used: AST parse on every project `.py`, import test on every module, signature
inspection on every public callable, targeted runtime smoke tests, and cross-file static
analysis of schema/callsite mismatches.

Modules / files inspected (45 project `.py` files):

```
config.py, inngest_client.py, main.py, login.py, run.py, run_scraper.py,
github_trigger.py, debug_extract.py, debug_feed.py, debug_feed_deep.py,
debug_feed_full.py, debug_menu.py, debug_urn_in_html.py,
test_connect_manual.py, test_feed_scout.py, test_recent_jobs.py,
agents/{auto_apply, connection, content, feed_scout, job_hunter, referral,
        __init__}.py,
browser/{easy_apply, interactions, manager, vision_fallback, __init__}.py,
core/{action_queue, budget, circuit_breaker, warmup, __init__}.py,
db/__init__.py,
llm/{service, __init__}.py,
mailer/{email, __init__}.py,
scrapers/{feed, jobs, jobs_recent, people, __init__}.py,
slack/{bot, __init__}.py
```

What passes:

- AST parse: 45/45 OK (no syntax errors anywhere).
- Module import: 27/29 OK. Two failures: `run_scraper.py` and `github_trigger.py`
  (rooted in real bugs documented below, not import side effects).
- `db.*` Pydantic schemas all construct cleanly with required fields.
- `core.action_queue.BUDGET_MAP` covers every `action_type` written by any agent
  (`connect`, `like`, `comment`, `view_profile`, `repost`, `easy_apply`,
  `search`).
- FastAPI app exposes the expected routes (`/api/inngest`, `/slack/commands`,
  `/slack/actions`, `/health`).
- `Pydantic v2` shared-default footgun for `DailyBudgets.likes` does **not** leak
  between instances (verified empirically).
- All 11 Inngest functions register on the FastAPI app via `main.ALL_FUNCTIONS`.
- Playwright `Locator.page` property exists on 1.44.0 (verified via
  `inspect.getattr_static`). My initial flag on `browser/easy_apply.py:173` was
  wrong and is **withdrawn**.

What follows is the bug list, ordered by severity. Line numbers are anchors,
not refactor targets — the goal is for someone to open each file and see the
exact thing called out.

---

## BLOCKERS — break the feature outright or cause real damage

### 1. `agents/referral.py:135` — referral email always goes to "" (empty recipient)

```python
email_sent = await step.run(
    "send-email", send_referral_email, "", name, company, target_role,
)
```

`send_referral_email(to_email, to_name, company, role)` is being called with
`to_email=""`. In `DRY_RUN` mode this short-circuits to `True` and the campaign
logs success (`referral_email_sent=True`). In production, `smtplib.sendmail`
gets an empty recipient and either raises `SMTPRecipientsRefused` or silently
drops the message. The campaign state then either logs `email_sent=False`
forever, or worse — gets stuck halfway with no retry.

Root cause: there is no email-resolution step in the pipeline at all. The
campaign discovers LinkedIn profiles via `search_company_employees` (which
returns `name / headline / company / linkedin_url`) — none of those include an
email. The `to_email` parameter was wired but no upstream component populates
it.

Fix sketch:
- Add an email-discovery step (Hunter / Apollo / a scraped public profile pass)
  before queuing the email.
- Store the resolved email on `ReferralTarget` (no field exists for it today —
  see issue #15).
- In `referral_on_connection_accepted`, look up the resolved email and pass it
  into `send_referral_email`.

---

### 2. `run_scraper.py:39` — `asyncio.run(main())` at module top level

```python
asyncio.run(main())
```

There is no `if __name__ == "__main__":` guard. Importing the module fires a
full LinkedIn scrape (launches Playwright, navigates, scrapes). Any tooling that
walks modules (pytest collection, linters, IDE indexers, the diagnostic harness
I just ran) accidentally triggers a real session. During this audit my own
import test really did launch a browser and scrape the feed — output in the
test log shows `[feed] found 6 candidate containers at
https://www.linkedin.com/feed/`.

Fix:
```python
if __name__ == "__main__":
    asyncio.run(main())
```

Same shape applies to the three repo-root "test" scripts — see issue #6.

---

## HIGH — feature is broken on the current LinkedIn layout

### 3. `scrapers/jobs.py:25` — stale `.jobs-search-results-list` selector

```python
await page.locator(".jobs-search-results-list").click()
for _ in range(3):
    await page.keyboard.press("PageDown")
```

LinkedIn's 2025/2026 jobs page renders the list inside
`.scaffold-layout__list-container ul` (or the generic `li[componentkey]`
container). `.jobs-search-results-list` does not exist on the current layout,
so:

1. The `click()` raises `TimeoutError` after the default wait.
2. The exception is swallowed by the `try / except Exception: pass` at
   line 29-30.
3. The PageDown presses never happen.
4. Only the cards visible above the fold (~6) are processed; everything else
   stays unscrolled.

Result: `scrape_jobs` quietly returns the top of the page and misses the long
tail. `job_hunter_run` therefore evaluates 6 jobs per keyword/location instead
of `max_jobs=10`.

Fix: replace the selector with `.scaffold-layout__list-container` (or `ul.scaffold-layout__list-container`), and switch from `click → PageDown` to
`element.evaluate("(el) => el.scrollBy(0, el.clientHeight)")` for stability.

---

### 4. `scrapers/people.py:25` — stale `li.reusable-search__result-container`

```python
containers = await page.locator("li.reusable-search__result-container").all()
```

The `reusable-search__result-container` class has been retired on the new
people-search layout. The locator returns `[]`, the loop is a no-op, and
`scrape_people_search` returns `[]`. `search_people` (the agent-facing wrapper)
therefore always returns `[]`, which means:

- `connection_agent_run` queues zero `connect` actions.
- `referral_campaign_start`'s `search_company_employees` step returns `[]`, so
  no referral campaign ever has targets.

Fix sketch:
```python
containers = await page.locator(
    "div[data-chameleon-result-urn], li[componentkey]"
).all()
```
(and update the inner selectors — `entity-result__title-text`, etc. — likewise).

---

### 5. `github_trigger.py:7` — `requests` not installed / not in `requirements.txt`

```python
import requests
```

`requirements.txt` does not list `requests`. `python -c "import requests"` from
inside the project venv raises `ModuleNotFoundError`. Anyone running
`github_trigger.run_remote_scrape(...)` gets an ImportError immediately.

Fix:
- Either add `requests>=2.31` to `requirements.txt`,
- Or migrate the four call sites to `httpx` (already a transitive dep of FastAPI).

---

### 6. `test_connect_manual.py`, `test_feed_scout.py`, `test_recent_jobs.py`

All three end with a bare `asyncio.run(main())` at module top level and have no
`if __name__ == "__main__":` guard. They are sitting at the repo root (not
inside `tests/`), so `pytest.ini`'s `testpaths = tests` directive doesn't
collect them — *but* anything that does `import test_connect_manual` (a quick
ad-hoc `python -c`, or a tool indexing the workspace) immediately launches a
real LinkedIn session and, in `test_connect_manual.py`'s case, sends an actual
connection request to a hard-coded profile URL.

Fix: same as issue #2 — wrap in `if __name__ == "__main__":`.

---

### 7. `run_scraper.py:36` — `print(...)` uses `→` (U+2192) which crashes on Windows console

```python
print(f"Saved {len(results)} results → {out_path}")
```

Default Windows `cmd` / PowerShell console encoding is `cp1252`, which can't
encode `→`. Running this on Windows raises:

```
UnicodeEncodeError: 'charmap' codec can't encode character '→' in position 16
```

I hit the exact same crash myself in the diagnostic harness (line numbers
above include a fixed version). Same risk in `login.py` (`✓`, `⚠`) — see
issue #11.

Fix: replace `→` with `->` (cheapest) or set
`sys.stdout.reconfigure(encoding="utf-8")` at process start.

---

### 8. `agents/content.py:88-89` — engage-list reactions silently no-op forever

```python
last_post_url = member.get("last_post_url")
if not last_post_url:
    continue
```

Nothing in the codebase ever populates `EngageListMember.last_post_url`. The
only writer to `engage_list` is `agents/referral.py:91`, which initializes it
with `"last_post_url": None`. There is no scheduled job that walks each engage
list member, pulls their newest activity (via the **existing**
`scrapers/feed.py:scrape_user_latest_post`), and writes the URL back. So every
iteration of `content_agent_reactions` hits `continue` for every member and
queues zero likes / zero comments.

Wired but dead. Either:
- Add a "refresh engage-list activity" Inngest cron that calls
  `scrape_user_latest_post(member['linkedin_url'])` and updates
  `last_post_url` + `last_post_content`, or
- Remove the dead engage-reactions cron until that's built.

---

### 9. `agents/content.py:101` — `last_post_content` is never written either

```python
comment = generate_engage_comment(
    member["name"], member.get("last_post_content", "their recent post"),
)
```

Same root cause as #8 — `last_post_content` is read but no code path writes it.
Even if `last_post_url` is fixed, the LLM is asked to comment on "their recent
post" generically. The resulting comments are vague and obviously generated.

---

## MEDIUM — wrong behaviour but the feature still kind of works

### 10. `agents/{connection, job_hunter, auto_apply, referral}.py` — sync Groq calls inside async coroutines

`llm/service.py` defines every helper as `def` (sync). Each call to
`_chat()` hits Groq's REST API and blocks. When these are called from inside
the async Inngest function bodies (e.g.
`generate_connection_note(...)` inside `connection_agent_run`), the asyncio
event loop is blocked for the duration of the HTTP round-trip
(~200–800 ms per call). With a 20-profile loop this stalls everything else
on the loop for ~10 s.

Fix sketch:
- Either wrap each LLM call with `await asyncio.to_thread(...)`,
- Or switch `llm/service.py` to `groq.AsyncGroq` and make the helpers async.

The same blocking concern applies to `mailer/email.py:send_referral_email`
(uses sync `smtplib`).

---

### 11. `login.py` — multiple non-ASCII characters in `print(...)` (`✓`, `⚠`, `─`)

Same Windows `cp1252` console crash as issue #7. Running `python login.py` in
a default PowerShell session fails midway through the script — the user
sees a `UnicodeEncodeError` traceback, the session never gets saved.

Fix: ASCII fallback, or:
```python
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
```
at the top.

---

### 12. `agents/connection.py:34-91` — Inngest-retry duplication

The for-loop over `all_profiles` calls
- `score_connection_profile(...)` (LLM call, sync),
- `generate_connection_note(...)` (LLM call, sync),
- `db.connections.update_one(... upsert=True)`,
- `ActionQueue.push(...)` twice (view_profile + connect),
- `db.connections.update_one(... status='queued')`

…all **outside `step.run`**. Inngest re-runs the entire function on retry. The
upserts are idempotent (Mongo dedups on `linkedin_url`), but `ActionQueue.push`
has no idempotency key — a retry duplicates the queued actions and burns the
daily budget twice. The LLM cost is also doubled per retry.

Same pattern in `agents/job_hunter.py:33-88`, `agents/auto_apply.py:39-101`,
`agents/feed_scout.py:34-84`. Fix: each per-item block should live inside
`step.run("process-" + linkedin_url, ...)`.

---

### 13. `agents/content.py:64` — `send_alert` called outside `step.run`

```python
await send_alert(f"Auto-reposted {len(auto_reposted)} posts. ...")
```

Same Inngest-retry duplication concern: on retry, Slack will be hit again with
the same auto-repost summary. Slack is rate-limited per channel, so it's
unlikely to break anything, but the operator sees double-notifications.

---

### 14. `mailer/email.py` — package name `mailer.email` shadows stdlib `email`

The package file is `mailer/email.py` and it itself does
`from email.mime.text import MIMEText`. This works today because Python
defaults to absolute imports, but:
- A future absolute → relative resolver change (e.g. `__future__` import
  added upstream) would shadow stdlib `email`.
- Anyone running `python -m mailer.email` from a script that already imported
  stdlib `email` may see surprising MRO issues.
- IDEs / type-checkers sometimes resolve the local file first.

Rename to `mailer/smtp.py` or `mailer/sender.py` and update the
`from mailer.email import send_referral_email` in `agents/referral.py:10`.

---

### 15. `db.Connection` schema is missing `template_used`; `agents/connection.py:64` writes it anyway

```python
"template_used": template,
```

Pydantic schema doesn't define this field (the closest is `outreach_template_used`
on the unrelated `ReputationScore`). Motor stores it raw because it bypasses
Pydantic on write, but anyone validating documents against the schema later
(e.g. via `Connection.model_validate(...)`) will get a silent drop with
`extra='ignore'` (the default) or a hard fail with `extra='forbid'`.

Symmetric issue: `recent_jobs`, `feed_job_posts`, and `easy_apply_logs`
collections all have **no schemas at all** in `db/__init__.py` — only indexes.
Writers (`agents/auto_apply.py`, `agents/feed_scout.py`,
`browser/easy_apply.py:_log_attempt`) use raw dicts with hand-rolled field
sets. The naming is also inconsistent — see #16.

---

### 16. Field-name drift across collections: `slack_message_ts` vs `slack_ts`

- `db.Job.slack_message_ts: Optional[str]`
- `agents/job_hunter.py:83` writes `"slack_message_ts": slack_ts` ✓
- `agents/auto_apply.py:69,99` writes `"slack_ts"` on `recent_jobs`
- `agents/feed_scout.py:52,70,78` writes `"slack_ts"` on `feed_job_posts`

So two distinct field names exist for the same concept across three
collections. Any cross-collection query / reporting code will get bitten by
this.

Pick one (`slack_ts` is shorter) and refactor all writers.

---

### 17. `datetime.datetime.utcnow()` used in 10+ files — deprecated in Python 3.12, removed in 3.14

Files using `datetime.utcnow()`:
```
main.py, agents/auto_apply.py, agents/connection.py, agents/content.py,
agents/feed_scout.py, agents/job_hunter.py, agents/referral.py,
browser/easy_apply.py, core/action_queue.py, core/budget.py,
core/circuit_breaker.py, core/warmup.py, db/__init__.py, slack/bot.py
```

Empirical test confirmed: this raises a `DeprecationWarning` on Python 3.12.5
(the version in the project venv). It will be **removed** in Python 3.14.

Fix:
```python
from datetime import datetime, timezone
datetime.now(timezone.utc)
```

Also affects `default_factory=datetime.utcnow` on `db.ActionQueueItem.created_at`,
`db.Job.discovered_at`, `db.ReferralCampaign.created_at`,
`db.ReputationScore.updated_at`.

---

### 18. `browser/easy_apply.py:168-170` — silent no-op when resume file missing

```python
async def _handle_resume(modal, resume_path: str) -> None:
    if not resume_path or not os.path.exists(resume_path):
        return
```

When `APPLICANT_RESUME_PATH` points at a missing file (or is unset and the
default `./resume.pdf` is missing), the resume-upload step quietly succeeds.
The walker advances to the next Easy Apply step thinking the upload happened.
Most Easy Apply forms then either reject the submit (consuming an application
slot in the user's daily budget) or — worse — submit without a resume.

Fix: surface a `needs_human` reason so the walker can `_dismiss_modal` cleanly.
Better yet, pre-validate `APPLICANT_RESUME_PATH` at startup in `main.lifespan`
and abort the auto-apply cron if missing.

---

## LOW — easy wins / code smell

### 19. `config.py:31,49,50,51,61` — `int(os.getenv("...", "..."))` crashes on empty env var

Verified empirically: `int(os.getenv("APPLICANT_YOE", "2"))` raises
`ValueError: invalid literal for int() with base 10: ''` when the env var is
defined as an empty string. Common in Docker compose files and CI.

Fix:
```python
_yoe = os.getenv("APPLICANT_YOE") or "2"
int(_yoe)
```
Same pattern in `WARMUP_WEEK`, `AUTO_REPOST_THRESHOLD`,
`CONNECTION_THRESHOLD`, `JOB_SLACK_THRESHOLD`.

---

### 20. `browser/easy_apply.py:155` — `modal if True else None` is dead-code conditional

```python
await _dismiss_modal(page, modal if True else None)
```

The conditional always evaluates to `modal`. Drop the `if True else None`.

---

### 21. `scrapers/feed.py:199` — `import_re = __import__('re')` shadow import

`re` is already imported at the top of the file. The inner
`import_re = __import__('re')` inside `_extract_content` is redundant.

---

### 22. `scrapers/feed.py:300-310` — `_post_container_selectors` is dead code

Function is defined but never called from anywhere in the codebase — the real
work happens in `_find_post_elements`. Delete it.

---

### 23. `core/circuit_breaker.py:21` — `auto_resume_hours: int = None` type-hint mismatch

```python
async def trip(level: str, reason: str, auto_resume_hours: int = None):
```

Default is `None` but the annotation says `int`. Should be `int | None = None`
(or `Optional[int]`). Strict type-checkers flag this.

---

### 24. `config.py:67-68` — boolean profile fields stored as strings

```python
"willing_to_relocate": os.getenv("APPLICANT_RELOCATE", "false"),
"requires_sponsorship": os.getenv("APPLICANT_SPONSORSHIP", "false"),
```

These end up in `config.APPLICANT_PROFILE` as the literal string
`"false"` / `"true"`. The LLM in
`llm.service.answer_application_question` formats them into the prompt as-is,
so it works for now. But any future code that does
`if profile["requires_sponsorship"]:` evaluates `bool("false") == True` and
silently asks for sponsorship anywhere a yes/no field is encountered.

---

### 25. `agents/feed_scout.py:27` — `lambda: scrape_organic_feed(max_posts=40)` retries scrape on every Inngest retry

The whole feed scrape is wrapped in `step.run("scrape-feed", lambda: ...)`,
which is correct — but `scrape_organic_feed`'s `enrich_urns=True` (the
default) clicks the per-post overflow menu on every retry. Multiple retries
within a short window look bot-y to LinkedIn. Consider `enrich_urns=False`
inside the cron path and only call the URN-enriching variant when actually
needed for queuing follow-up actions.

---

### 26. `slack/bot.py:8` — `SLACK_BOT_TOKEN` read with `os.getenv`, but everywhere else uses `config.SLACK_BOT_TOKEN`

```python
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
```

Module-level read happens at import time; if `dotenv` hasn't loaded yet (it
loads in `config.py` import), this can be `None` even when `.env` defines it.
In practice `config` is imported first by every entry point, so it works —
but it's brittle.

Use `from config import config` and `config.SLACK_BOT_TOKEN`.

---

### 27. `db/__init__.py:9` — `AsyncIOMotorClient` instantiated at module-import time

Doesn't fail at import (Motor lazy-connects on first I/O), but:
- Any process that imports `db` opens a client handle it never explicitly closes.
- `MONGODB_URI` is read at import — `/dryrun` and friends can't switch
  databases at runtime.

Move to a lazy `get_db()` accessor if you ever want multi-tenant or hot config
reload. Otherwise just document the implicit handle.

---

## INFO — design observations, not bugs

### 28. `slack/` local package shadows the `slack_sdk` import (verified safe)

`slack_sdk` is a separate top-level package; Python's absolute import order
means `from slack_sdk.web.async_client import AsyncWebClient` resolves
correctly even though the project also has a local `slack/` package. Verified
at runtime (`slack_sdk 3.27.2`).

### 29. `inngest_client.py` builds the client with `is_production = bool(INNGEST_SIGNING_KEY)`

This means dev mode is implicit. Fine for now, but be aware that
`main.lifespan` doesn't log which mode is active.

### 30. `inngest.Step.run`'s `*handler_args` are supported

Verified at runtime — the signature is
`(self, step_id, handler, *handler_args) -> JSONT`. So the various
`step.run("name", fn, arg1, arg2, ...)` callers are well-formed. The bigger
concern is that the handler's return must be JSON-serialisable. Spot-checked
all current handlers (return `list[dict]`, `bool`, `str`, `dict` of primitives)
— all OK.

---

## Suggested triage order

1. Fix issues **#2, #6, #7, #11** first — they break the local dev loop or
   cause data damage on import. ~30 minutes of work.
2. Then **#1** — referral campaigns silently send no emails. The whole
   referral subsystem is dead without it.
3. Then **#3, #4** — both scrapers return empty lists on the current
   LinkedIn layout, which silently kills the connection and job-hunter
   agents.
4. Then **#5, #18** — both block real Easy-Apply / GitHub-Actions usage.
5. Then **#8, #9** — bring the content engagement agent back to life or
   delete the dead cron.
6. Everything else (medium / low) can be batched.

---

## Reproducers

All findings above were generated by three scripts left in the project root:

- `_diagnostic_runner.py` — AST + import + signature + smoke test sweep.
- `_deep_check.py` — static-text scan for the bug patterns above.
- `_runtime_check.py` / `_runtime_check2.py` — actual interpreter probes
  (Pydantic shared-default, empty-env-var `int()`, deprecation warning,
  Playwright API surface).

Re-run with:

```
.\venv\Scripts\python.exe _diagnostic_runner.py
.\venv\Scripts\python.exe _deep_check.py
.\venv\Scripts\python.exe _runtime_check.py
.\venv\Scripts\python.exe _runtime_check2.py
```

Output also written to `_diagnostic_results.json` and `_deep_check_results.json`.

These four `_*.py` files were added by this audit and can be deleted (or kept
as a regression harness) once the issues are addressed.
