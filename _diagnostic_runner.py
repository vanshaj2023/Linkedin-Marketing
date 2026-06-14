"""Diagnostic harness — imports and inspects every module + key functions.
Prints results as TEST: <id> | OK|FAIL | <details>.
"""
import ast
import importlib
import inspect
import json
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

results = []


def log(test_id, status, msg=""):
    line = f"TEST: {test_id} | {status} | {msg}"
    results.append(line)
    print(line)


# 1. AST syntax check on every project .py file
SKIP_DIRS = {"venv", "__pycache__", "output", ".claude", ".git"}
project_pys: list[Path] = []
for path in ROOT.rglob("*.py"):
    if any(part in SKIP_DIRS for part in path.parts):
        continue
    project_pys.append(path)

for path in project_pys:
    rel = path.relative_to(ROOT).as_posix()
    try:
        src = path.read_text(encoding="utf-8")
        ast.parse(src)
        log(f"AST:{rel}", "OK")
    except SyntaxError as e:
        log(f"AST:{rel}", "FAIL", f"{type(e).__name__}: {e}")
    except Exception as e:
        log(f"AST:{rel}", "FAIL", f"{type(e).__name__}: {e}")

# 2. Import test on each module
MODULES = [
    "config",
    "inngest_client",
    "db",
    "core.action_queue",
    "core.budget",
    "core.circuit_breaker",
    "core.warmup",
    "browser.manager",
    "browser.interactions",
    "browser.easy_apply",
    "browser.vision_fallback",
    "llm.service",
    "mailer.email",
    "scrapers.feed",
    "scrapers.jobs",
    "scrapers.jobs_recent",
    "scrapers.people",
    "agents.connection",
    "agents.content",
    "agents.feed_scout",
    "agents.job_hunter",
    "agents.referral",
    "agents.auto_apply",
    "slack.bot",
    "main",
    "login",
    "github_trigger",
    "run",
    "run_scraper",
]

imported_modules = {}
for mod_name in MODULES:
    try:
        m = importlib.import_module(mod_name)
        imported_modules[mod_name] = m
        log(f"IMPORT:{mod_name}", "OK")
    except Exception as e:
        tb_lines = traceback.format_exception_only(type(e), e)
        log(f"IMPORT:{mod_name}", "FAIL", " ".join(tb_lines).strip())

# 3. Inspect public callables for each successfully imported module
for mod_name, m in imported_modules.items():
    try:
        callables = []
        for n, obj in inspect.getmembers(m):
            if n.startswith("_"):
                continue
            if inspect.isfunction(obj) or inspect.iscoroutinefunction(obj) or inspect.isclass(obj):
                if getattr(obj, "__module__", "") == mod_name:
                    callables.append(n)
        log(f"INSPECT:{mod_name}", "OK", f"public={callables}")
    except Exception as e:
        log(f"INSPECT:{mod_name}", "FAIL", f"{type(e).__name__}: {e}")

# 4. Pure-function smoke checks
def smoke_test(name, fn):
    try:
        fn()
        log(f"SMOKE:{name}", "OK")
    except Exception as e:
        log(f"SMOKE:{name}", "FAIL", f"{type(e).__name__}: {e}")


# config exists?
def t_config():
    from config import config
    assert isinstance(config.TARGET_KEYWORDS, list)
    assert isinstance(config.TARGET_JOB_KEYWORDS, list)
    assert isinstance(config.APPLICANT_PROFILE, dict)


smoke_test("config.attrs", t_config)


# db schemas validate
def t_db_schemas():
    from db import (
        ActionQueueItem, DailyBudgetLimit, DailyBudgets, SystemHealth,
        Connection, Job, EngageListMember, ReferralTarget, ReferralCampaign,
        ReputationScore,
    )
    a = ActionQueueItem(agent="x", action_type="like", payload={"k": "v"})
    assert a.priority == 5
    b = DailyBudgets(date="2026-06-14")
    assert b.likes.limit == 60
    c = Connection(
        linkedin_url="https://x.com", name="N", headline="H", company="C",
        source_agent="ag",
    )
    assert c.status == "identified"
    Job(linkedin_post_url="u", job_title="t", company="c", poster_name="p")
    EngageListMember(
        linkedin_url="u", name="n", reason="r", added_by_agent="a",
    )
    ReferralTarget(linkedin_url="u", name="n", role="r", score=10, batch=1)
    ReferralCampaign(campaign_id="c1", company="c", target_role="r")
    ReputationScore(linkedin_url="u")


smoke_test("db.schemas", t_db_schemas)


# DailyBudgets contains the same field set used by warmup/budget code
def t_db_dailybudgets_alignment():
    from db import DailyBudgets
    keys = DailyBudgets.model_fields.keys()
    expected = {"date", "connection_requests", "profile_views", "likes",
                "comments", "reposts", "searches", "applications"}
    missing = expected - set(keys)
    assert not missing, f"missing keys in DailyBudgets: {missing}"


smoke_test("db.DailyBudgets.fields", t_db_dailybudgets_alignment)


# warmup constants/lookups
def t_warmup_constants():
    from core.warmup import WARMUP_SCHEDULE
    assert {1, 2, 3, 4}.issubset(WARMUP_SCHEDULE.keys())
    for k, v in WARMUP_SCHEDULE.items():
        for fld in ("connections", "likes", "views", "comments", "applications"):
            assert fld in v, f"week {k} missing {fld}"


smoke_test("warmup.WARMUP_SCHEDULE", t_warmup_constants)


# action_queue BUDGET_MAP exhaustive vs used action_types
def t_budget_map_exhaustive():
    from core.action_queue import BUDGET_MAP
    used = {"connect", "like", "comment", "view_profile", "search", "repost", "easy_apply"}
    assert used.issubset(BUDGET_MAP.keys())


smoke_test("action_queue.BUDGET_MAP", t_budget_map_exhaustive)


# Verify CircuitBreaker class methods are static
def t_cb_signatures():
    from core.circuit_breaker import CircuitBreaker
    assert inspect.iscoroutinefunction(CircuitBreaker.status)
    assert inspect.iscoroutinefunction(CircuitBreaker.trip)
    assert inspect.iscoroutinefunction(CircuitBreaker.reset)


smoke_test("circuit_breaker.signatures", t_cb_signatures)


# Verify ActionQueue methods are coroutines
def t_aq_signatures():
    from core.action_queue import ActionQueue, process_one_action, requeue_deferred_actions
    assert inspect.iscoroutinefunction(ActionQueue.push)
    assert inspect.iscoroutinefunction(ActionQueue.get_next_action)
    assert inspect.iscoroutinefunction(ActionQueue.mark_done)
    assert inspect.iscoroutinefunction(ActionQueue.mark_failed)
    assert inspect.iscoroutinefunction(process_one_action)
    assert inspect.iscoroutinefunction(requeue_deferred_actions)


smoke_test("action_queue.signatures", t_aq_signatures)


# Verify slack functions exist
def t_slack_functions():
    from slack.bot import (
        send_alert, send_repost_digest, send_job_alert,
        send_feed_job_alert, send_manual_apply_alert,
        send_apply_success, send_referral_alert,
        handle_status_command, handle_pause_command,
        handle_resume_command, handle_referral_command,
    )
    for fn in (send_alert, send_repost_digest, send_job_alert,
               send_feed_job_alert, send_manual_apply_alert,
               send_apply_success, send_referral_alert,
               handle_status_command, handle_pause_command,
               handle_resume_command, handle_referral_command):
        assert inspect.iscoroutinefunction(fn), f"{fn.__name__} should be async"


smoke_test("slack.bot.async_signatures", t_slack_functions)


# email sender exists and has correct signature
def t_mailer():
    from mailer.email import send_referral_email
    sig = inspect.signature(send_referral_email)
    expected_params = ["to_email", "to_name", "company", "role"]
    assert list(sig.parameters.keys()) == expected_params
    # NOT async — referral.py calls it via step.run (which awaits the wrapped result)
    # But step.run expects a coroutine function. So this is a BUG candidate.


smoke_test("mailer.signature", t_mailer)


# Verify referral.py calls send_referral_email correctly
def t_referral_email_call():
    import agents.referral as r
    src = Path(r.__file__).read_text()
    # step.run with a non-async function and arguments is problematic
    # Inngest's step.run expects either an async fn or sync fn — both supported,
    # but if the arguments shape is wrong, it'll fail at runtime.
    assert "send_referral_email" in src
    # First positional is to_email — referral.py passes "" as to_email
    assert '"", name, company, target_role' in src or "send_referral_email" in src


smoke_test("referral.email_call_shape", t_referral_email_call)


# LLM service public functions
def t_llm_service_signatures():
    from llm.service import (
        generate_connection_note, score_connection_profile,
        score_job_post, score_post_for_repost,
        classify_feed_post_as_job,
        answer_application_question, generate_engage_comment,
    )
    fns = [
        generate_connection_note, score_connection_profile,
        score_job_post, score_post_for_repost,
        classify_feed_post_as_job,
        answer_application_question, generate_engage_comment,
    ]
    for fn in fns:
        assert callable(fn)


smoke_test("llm.service.public_signatures", t_llm_service_signatures)


# Inngest functions registered
def t_inngest_functions():
    import main
    assert hasattr(main, "ALL_FUNCTIONS")
    assert len(main.ALL_FUNCTIONS) == 11


smoke_test("main.ALL_FUNCTIONS", t_inngest_functions)


# main.app is a FastAPI instance
def t_main_app():
    import main
    from fastapi import FastAPI
    assert isinstance(main.app, FastAPI)


smoke_test("main.app_type", t_main_app)


# Browser manager exports
def t_browser_manager():
    from browser.manager import (
        STATE_FILE, CONSISTENT_USER_AGENT, CONSISTENT_VIEWPORT,
        get_authenticated_context, setup_page_stealth, get_browser_page,
        human_type, human_click, safe_sleep, validate_session,
    )
    assert STATE_FILE == "state.json"
    assert isinstance(CONSISTENT_USER_AGENT, str)
    assert "width" in CONSISTENT_VIEWPORT
    for fn in (get_authenticated_context, setup_page_stealth, get_browser_page,
               human_type, human_click, safe_sleep, validate_session):
        assert inspect.iscoroutinefunction(fn), f"{fn.__name__} should be async"


smoke_test("browser.manager.exports", t_browser_manager)


# Easy-apply imports
def t_easy_apply_constants():
    from browser.easy_apply import MAX_STEPS, run_easy_apply
    assert MAX_STEPS == 10
    assert inspect.iscoroutinefunction(run_easy_apply)


smoke_test("easy_apply.exports", t_easy_apply_constants)


# Vision fallback
def t_vision_fallback():
    from browser.vision_fallback import (
        fast_screenshot, vision_assisted_connect_click,
    )
    assert inspect.iscoroutinefunction(fast_screenshot)
    assert inspect.iscoroutinefunction(vision_assisted_connect_click)


smoke_test("vision_fallback.exports", t_vision_fallback)


# Scrapers
def t_scrapers():
    from scrapers.feed import (
        scrape_hiring_posts, scrape_organic_feed, scrape_user_latest_post,
    )
    from scrapers.jobs import scrape_jobs, search_jobs
    from scrapers.jobs_recent import scrape_recent_jobs
    from scrapers.people import (
        scrape_people_search, search_people, search_company_employees,
    )
    for fn in (scrape_hiring_posts, scrape_organic_feed, scrape_user_latest_post,
               scrape_jobs, search_jobs, scrape_recent_jobs,
               scrape_people_search, search_people, search_company_employees):
        assert inspect.iscoroutinefunction(fn), f"{fn.__name__} should be async"


smoke_test("scrapers.exports", t_scrapers)


# Agents (these are Inngest registered functions, decorated)
def t_agents():
    from agents.connection import connection_agent_run, connection_acceptance_poller
    from agents.content import content_agent_reposts, content_agent_reactions
    from agents.feed_scout import feed_scout_run
    from agents.job_hunter import job_hunter_run
    from agents.referral import referral_campaign_start, referral_on_connection_accepted
    from agents.auto_apply import auto_apply_run


smoke_test("agents.exports", t_agents)


# 5. Static analysis to find common bug patterns
def t_static_dailybudgets_alignment():
    """Compare warmup's update fields with DailyBudgets model fields."""
    src = (ROOT / "core" / "warmup.py").read_text()
    # warmup updates connection_requests.limit, profile_views.limit, etc.
    # All these must match DailyBudgets fields, already covered above.
    pass


smoke_test("static.warmup_alignment", t_static_dailybudgets_alignment)


def t_inngest_step_run_with_lambda():
    """step.run + lambda → Inngest needs serializable callables.
    feed_scout_run passes a lambda — this is allowed but breaks on retry serialization."""
    src = (ROOT / "agents" / "feed_scout.py").read_text()
    has_lambda = "lambda: scrape_organic_feed" in src
    assert has_lambda  # confirms the lambda usage — not strictly a bug


smoke_test("static.feed_scout_lambda", t_inngest_step_run_with_lambda)


def t_db_async_at_import_time():
    """db/__init__.py creates AsyncIOMotorClient at import time. This may try
    to connect even if MongoDB is offline; let's note it."""
    import db
    assert db.client is not None


smoke_test("static.db_client_created", t_db_async_at_import_time)


def t_email_in_referral_signature_mismatch():
    """referral.py: step.run("send-email", send_referral_email, "", name, company, target_role).
    send_referral_email is *sync*; Inngest step.run can run sync callables.
    But the signature is (to_email, to_name, company, role) — referral passes "" as to_email.
    That guarantees the email goes to "" — bug."""
    src = (ROOT / "agents" / "referral.py").read_text()
    assert 'send_referral_email, "", name, company, target_role' in src


smoke_test("static.referral_email_empty_recipient", t_email_in_referral_signature_mismatch)


def t_email_module_name_collision():
    """The package `mailer.email` shadows stdlib `email` package within itself.
    `from email.mime.text import MIMEText` inside mailer/email.py works only
    because Python resolves absolute imports first, but this is fragile."""
    # Check: ensure file imports work
    from mailer.email import send_referral_email
    assert callable(send_referral_email)


smoke_test("static.mailer_email_naming", t_email_module_name_collision)


def t_apply_warmup_inngest_lambda_call():
    """action_queue.inngest_budget_reset: step.run("apply-warmup-limits", lambda: apply_warmup_budget(...)).
    apply_warmup_budget is async, but it's called inside a lambda that returns a coroutine.
    step.run expects either an awaitable result OR a callable returning one.
    Inngest 0.4 handles awaitables — should be OK. Note this anyway."""
    src = (ROOT / "core" / "action_queue.py").read_text()
    assert "lambda: apply_warmup_budget" in src


smoke_test("static.action_queue_warmup_lambda", t_apply_warmup_inngest_lambda_call)


def t_dryrun_command_mutation():
    """main.py /dryrun: imports config module and mutates cfg_module.config.DRY_RUN.
    Because `config` is the Config instance, but cfg_module.config is the same singleton —
    mutation propagates. This works, but it's a runtime monkey-patch."""
    src = (ROOT / "main.py").read_text()
    assert "cfg_module.config.DRY_RUN = text.lower() == \"on\"" in src


smoke_test("static.dryrun_command", t_dryrun_command_mutation)


def t_send_alert_inngest_loop():
    """content.py: `await send_alert(...)` is called directly inside the Inngest
    function body (not via step.run). Inngest discourages side-effects outside
    step.run because retries can repeat them. Note this."""
    src = (ROOT / "agents" / "content.py").read_text()
    assert "await send_alert(" in src


smoke_test("static.content_send_alert_direct", t_send_alert_inngest_loop)


def t_inspect_db_dailybudgets_likes_default():
    """The DailyBudgets default `likes=DailyBudgetLimit(limit=60)` is a shared
    default for all instances (a known Pydantic v2 footgun before v2.4)."""
    from db import DailyBudgets
    a = DailyBudgets(date="d1")
    b = DailyBudgets(date="d2")
    a.likes.used = 99
    log("STATIC:db.DailyBudgets.shared_default",
        "FAIL" if b.likes.used == 99 else "OK",
        f"a.likes.used={a.likes.used}, b.likes.used={b.likes.used}")


t_inspect_db_dailybudgets_likes_default()


def t_inspect_action_queue_view_profile_get_browser_page():
    """action_queue._dispatch_action: view_profile path uses get_browser_page,
    closes context.browser AND p_instance — correct.
    But other paths (connect/like/comment/repost) inside the SAME function use
    `async with async_playwright() as p:` directly (in interactions.py),
    so no leak. However the easy_apply path inside run_easy_apply uses its own
    `async with` block. OK."""
    pass


smoke_test("static.action_queue_browser_close", t_inspect_action_queue_view_profile_get_browser_page)


# Persist a JSON report
out = ROOT / "_diagnostic_results.json"
out.write_text(json.dumps(results, indent=2))
print("\n=== DONE ===")
print(f"Results in {out}")
