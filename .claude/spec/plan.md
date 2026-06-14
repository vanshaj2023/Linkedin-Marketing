# LinkedIn Automation — Job Hunting + Referral Plan

## Current State (What Already Exists)

| Component | File | Status |
|---|---|---|
| Home feed scraper | `scrapers/feed.py` — `scrape_organic_feed()` | Working |
| Keyword/content search scraper | `scrapers/feed.py` — `scrape_hiring_posts()` | Working |
| LinkedIn Jobs search | `scrapers/jobs.py` — `search_jobs()` | Working (no 24h filter, no Easy Apply) |
| Job Hunter agent | `agents/job_hunter.py` | Runs 3x/day, scores jobs, alerts Slack, triggers referral if score>=80 |
| Connection agent | `agents/connection.py` | Runs 2x/day, queues connect via action_queue |
| Referral agent | `agents/referral.py` | Discovers employees, batches over 5-7 days, queues connects + email |
| `send_connection_request` | `browser/interactions.py` | Fixed and verified end-to-end |
| Action queue + budgets + circuit breaker | `core/` | Working, processes 1 action per 5 min |
| Slack alerts + slash commands + buttons | `slack/bot.py` + `main.py` | Working |

---

## Goals

1. **Continuously monitor feed** and surface job-related posts (not just formal job listings).
2. **Auto-connect to relevant people** (job posters, referral targets) using the now-working connect flow.
3. **New scraper for recent LinkedIn job listings** — auto-apply via Easy Apply, or send Slack link for manual apply if not possible.

---

## Phase A — Home Feed Job Scout

Scan the home feed every 30 minutes and flag posts where someone is hiring, offering referrals, or asking for DMs. These never show up in `/jobs`.

### A1. New `scrapers/feed_jobs.py`

Thin wrapper around existing `scrape_organic_feed()`. Add `author_url` extraction to `_extract_post_data` in `scrapers/feed.py`:
- Extract from `a[href*="/in/"]` inside actor block
- Store alongside `author_name` in the returned dict

### A2. New LLM function in `llm/service.py`

```python
def classify_feed_post_as_job(content: str, author_name: str) -> dict:
```

Returns:
```json
{
  "is_job_post": true,
  "role": "Backend Engineer",
  "company": "Stripe",
  "apply_method": "comment | dm | link | email | unknown",
  "apply_target": "DM author / link / email",
  "relevance_score": 0-100,
  "reasoning": "..."
}
```

Groq call. Skip posts with content < 80 chars before calling.

### A3. New `agents/feed_scout.py`

```
Trigger: TriggerCron(cron="*/30 8-22 * * *")

Flow:
  1. CircuitBreaker.status() != "green" → skip
  2. posts = scrape_organic_feed(max_posts=40)
  3. For each post:
     - Skip if db.feed_job_posts.find_one({post_id: post.post_id})
     - cls = classify_feed_post_as_job(content, author_name)
     - If cls.is_job_post and cls.relevance_score >= 60:
         ts = send_feed_job_alert({**post, **cls})
         db.feed_job_posts.insert_one({...})
     - Else: insert with is_job_post=False to skip on future cycles
```

### A4. New Slack alert in `slack/bot.py`

Function: `send_feed_job_alert(post: dict) -> str`
Channel: `#feed-job-alerts`

Blocks:
- Header: role + company (or "Hiring post")
- Body: author, relevance score, content snippet, why it matched
- Buttons:
  - `Connect with poster` → `action_id="connect_poster"`, value=`author_url::note_seed`
  - `Open post` → link
  - `Dismiss` → mark dismissed

In `main.py:/slack/actions`, add handler for `connect_poster`:
```python
elif action_id == "connect_poster":
    url, note_seed = value.split("::", 1)
    note = generate_connection_note(headline=note_seed, post_summary=note_seed, template_id="A")
    await ActionQueue.push("feed_scout", "connect", {"target_profile_url": url, "message": note}, priority=2)
```

### A5. New MongoDB collection `feed_job_posts`

```
{
  post_id: str (unique index),
  post_url: str,
  author_name: str,
  author_url: str | None,
  content_snippet: str,
  is_job_post: bool,
  classification: dict,
  slack_ts: str | None,
  status: "alerted" | "skipped" | "connected" | "dismissed",
  discovered_at: datetime,
  last_action_at: datetime | None
}
```

Add to `setup_indexes()` in `db/__init__.py`:
```python
await db.feed_job_posts.create_index("post_id", unique=True)
```

---

## Phase B — Referral Connect Verification

The `send_connection_request` fix is done. Now confirm the full pipeline works end-to-end.

### B1. Verify action queue routing

`core/action_queue.py:_dispatch_action` already routes `"connect"` to `send_connection_request`. No changes needed.

### B2. Verify referral agent pushes correctly

`agents/referral.py:81-87` pushes `view_profile` then `connect` per target. Confirmed correct.

### B3. Add `Connect to poster` button on existing job alert

In `slack/bot.py:send_job_alert`, add a fourth button alongside existing three:
```python
{"type": "button", "text": {"type": "plain_text", "text": "Connect to Poster"},
 "action_id": "connect_poster", "value": f"{job.get('poster_url', '')}::recruiter at {job['company']}"}
```

Add `poster_url` field to jobs scraper output (`scrapers/jobs.py`).

### B4. End-to-end smoke test

- Set `DRY_RUN=false`, budget `connection_requests.limit=3`
- Run `/referral <TestCompany>` in Slack
- Watch `db.action_queue` fill with view_profile + connect items
- Wait for Inngest queue-processor cron (every 5 min) to drain
- Confirm `connections.status` flips to `request_sent`
- Confirm no exception in Inngest logs

---

## Phase C — Recent Jobs Scraper + Auto-Apply

Two halves. Build C1 first — it's useful even without auto-apply.

### C1. New `scrapers/jobs_recent.py`

LinkedIn URL params for recent jobs:
- `f_TPR=r86400` — past 24 hours
- `sortBy=DD` — most recent first
- `f_AL=true` — Easy Apply only (toggle)

```python
async def scrape_recent_jobs(
    keywords: list[str],
    locations: list[str],
    max_per_combo: int = 25,
    easy_apply_only: bool = False,
) -> list[dict]:
```

Returns per job:
```
{
  job_title, company, linkedin_post_url, description,
  is_easy_apply: bool,
  external_apply_url: str | None,  # ATS link if not Easy Apply
  posted_ago_text: str,            # "2 hours ago" etc.
  poster_name: str,
  poster_url: str | None
}
```

Detection: check for `button` with text "Easy Apply" vs external `Apply` that opens a new tab to an ATS URL.

### C2. New `browser/easy_apply.py`

```python
async def apply_easy(
    page,
    job_url: str,
    profile_data: dict,
    save_debug_dir: str = "output/easy_apply",
) -> dict:
    """
    Returns {"ok": True} or {"ok": False, "reason": "needs_human | timed_out | unknown"}
    """
```

Walk the Easy Apply modal step by step:
1. Click `button[aria-label*="Easy Apply"]`
2. Loop up to 10 steps:
   - Detect current step header text
   - `"Contact info"` / `"Resume"` / `"Home address"` — fill known fields from `profile_data`, click Next
   - `"Additional questions"` — for each field: call `answer_application_question()`, bail on unknown required fields
   - `"Review your application"` — click Submit
   - On success modal → return `{ok: True}`
3. At each step, save screenshot to `output/easy_apply/{job_id}/step_{n}.png`

Safety rails:
- Hard cap: 10 steps max, then bail with `"needs_human"`
- If any required field is empty after LLM attempt → bail with `"needs_human"`
- Never submit if modal has any visible error message

### C3. New LLM function in `llm/service.py`

```python
def answer_application_question(
    question: str,
    field_type: str,                 # "text" | "number" | "yes_no" | "single_select" | "multi_select"
    options: list[str] | None = None,
    context: dict | None = None,     # applicant profile context
) -> str:
```

Uses `APPLICANT_PROFILE` from config. Returns a string; for select fields must be one of `options`.

### C4. New config in `config.py`

```python
APPLICANT_PROFILE: dict = {
    "phone": os.getenv("APPLICANT_PHONE", ""),
    "years_experience": int(os.getenv("APPLICANT_YOE", "2")),
    "current_role": os.getenv("APPLICANT_ROLE", ""),
    "resume_path": os.getenv("APPLICANT_RESUME_PATH", "./resume.pdf"),
    "linkedin_url": os.getenv("APPLICANT_LINKEDIN_URL", ""),
    "github_url": os.getenv("APPLICANT_GITHUB_URL", ""),
    "salary_expectation": os.getenv("APPLICANT_SALARY", "Negotiable"),
    "willing_to_relocate": os.getenv("APPLICANT_RELOCATE", "false"),
    "requires_sponsorship": os.getenv("APPLICANT_SPONSORSHIP", "false"),
}
```

### C5. New `agents/auto_apply.py`

```
Trigger: TriggerCron(cron="0 */2 * * *")   # every 2 hours

Flow:
  1. CircuitBreaker.status() != "green" → skip
  2. jobs = scrape_recent_jobs(config.TARGET_JOB_KEYWORDS, config.TARGET_JOB_LOCATIONS, max=20)
  3. For each job not already in db.recent_jobs:
     - score_data = score_job_post(title, company, description, "")
     - relevance = score_data["relevance_score"]
     - insert stub into db.recent_jobs with status="evaluating"
     - If relevance < 65: update status="low_score", skip
     - If job.is_easy_apply and relevance >= 75:
         ActionQueue.push("auto_apply", "easy_apply", {url, score, title, company}, priority=2)
         update status="queued_apply"
     - Else (external link OR easy_apply but score 65-74):
         send_manual_apply_alert(job, score_data)
         update status="slack_manual"
```

### C6. Extend `core/action_queue.py:_dispatch_action`

```python
elif action_type == "easy_apply":
    from browser.easy_apply import apply_easy
    from browser.manager import get_browser_page
    page, ctx, pw = await get_browser_page(headless=True)
    try:
        await page.goto(payload["url"])
        res = await apply_easy(page, payload["url"], config.APPLICANT_PROFILE)
    finally:
        await ctx.browser.close()
        await pw.stop()
    if not res.get("ok"):
        if res.get("reason") == "needs_human":
            await send_alert(f"Easy Apply needs human: {payload['title']} @ {payload['company']}\n{payload['url']}", level="warn")
        raise Exception(f"Easy Apply failed: {res.get('reason')}")
    await db.recent_jobs.update_one(
        {"linkedin_post_url": payload["url"]},
        {"$set": {"status": "applied", "applied_at": datetime.utcnow()}},
    )
```

Add to `BUDGET_MAP`:
```python
"easy_apply": "applications",
```

Add `applications: DailyBudgetLimit = DailyBudgetLimit(limit=10)` to `DailyBudgets` in `db/__init__.py`.

### C7. New Slack notifications in `slack/bot.py`

**Channel:** `CHANNEL_APPLY = "#auto-apply"`

Two functions:

`send_manual_apply_alert(job: dict, score_data: dict)` — external ATS or borderline score:
- Header: role + company + score
- Body: why it matched, posted when
- Buttons: `Open ATS` (external URL), `Mark Applied`, `Trigger Referral`

`send_apply_success(job: dict)` — after Easy Apply succeeds:
- Simple confirmation message with job title, company, Slack-thread in `#auto-apply`

### C8. New MongoDB collection `recent_jobs`

```
{
  linkedin_post_url: str (unique index),
  job_title, company, poster_name, poster_url,
  description: str,
  is_easy_apply: bool,
  external_apply_url: str | None,
  relevance_score: int,
  status: "evaluating" | "low_score" | "queued_apply" | "applied" | "slack_manual" | "failed",
  slack_ts: str | None,
  discovered_at: datetime,
  applied_at: datetime | None
}
```

Add to `setup_indexes()`:
```python
await db.recent_jobs.create_index("linkedin_post_url", unique=True)
await db.easy_apply_logs.create_index([("job_url", 1), ("attempted_at", -1)])
```

---

## Phase D — Config, Channels, Environment

### D1. New Slack channels to create

| Channel | Agent | Purpose |
|---|---|---|
| `#feed-job-alerts` | `feed_scout` | Hiring posts from home feed |
| `#auto-apply` | `auto_apply` | Easy Apply results + manual apply fallbacks |
| Keep `#job-alerts` | `job_hunter_run` | Formal LinkedIn Jobs search results |
| Keep `#referral-campaigns` | `referral_campaign_start` | Referral campaign progress |

### D2. New env vars to add to `.env`

```env
APPLICANT_PHONE=
APPLICANT_YOE=2
APPLICANT_ROLE=Full Stack Developer
APPLICANT_RESUME_PATH=./resume.pdf
APPLICANT_LINKEDIN_URL=
APPLICANT_GITHUB_URL=
APPLICANT_SALARY=Negotiable
APPLICANT_RELOCATE=false
APPLICANT_SPONSORSHIP=false
```

### D3. Register new agents in `main.py`

```python
from agents.feed_scout import feed_scout_run
from agents.auto_apply import auto_apply_run

ALL_FUNCTIONS = [
    ...existing...,
    feed_scout_run,
    auto_apply_run,
]
```

### D4. Final cron schedule

| Agent | Cron | Runs |
|---|---|---|
| `feed_scout_run` | `*/30 8-22 * * *` | Every 30 min, 8am-10pm |
| `auto_apply_run` | `0 */2 * * *` | Every 2 hours |
| `job_hunter_run` (existing) | `0 8,13,19 * * *` | 3x daily |
| `connection_agent_run` (existing) | `0 9,18 * * *` | 2x daily |
| `inngest_queue_processor` (existing) | `*/5 * * * *` | Every 5 min — drains all queues |
| `inngest_budget_reset` (existing) | `0 0 * * *` | Midnight daily |

---

## Build Order

1. **Phase B** (~1-2 hr): Smoke-test referral connect end-to-end in dry-run, then 3 real connects. Confirm the fix works in production.
2. **Phase A** (~4-6 hr): Feed scout is the highest signal-to-noise addition. Low risk, no new browser interactions.
3. **Phase C1 + C5 + C7** (~3 hr): Recent jobs scraper + Slack manual-apply fallback. Useful immediately, no auto-apply risk yet.
4. **Phase C2 + C3 + C4 + C6** (~6-8 hr): Easy Apply walker. Test extensively in dry-run. Cap at 3 applications/day initially.

---

## Risk Notes

- **Easy Apply form variability**: every company adds custom screening questions. Expect ~40% bail-to-Slack on first attempts. Track failures in `easy_apply_logs` and grow LLM answerer over time.
- **Auto-apply activity is loud**: LinkedIn sees it as a burst signal. Keep `applications` budget at 5-10/day max. Combined with connects + likes you are near the warm-up week limits in `plan.md`.
- **Resume upload**: uses Playwright `input[type=file].set_input_files(path)`. Resume file must exist at `APPLICANT_RESUME_PATH` on the machine running the server.
- **Feed scout URN deduplication**: posts without a real URN get a `componentkey:*` pseudo-ID. These are session-stable but not cross-session stable. The `discovered_at` + `content_snippet` combo acts as secondary dedup if `post_id` alone isn't enough.
- **Never auto-apply without reviewing the first 5 jobs manually**: run `DRY_RUN=true` for auto_apply initially and check `db.recent_jobs` to confirm scoring is calibrated before going live.
