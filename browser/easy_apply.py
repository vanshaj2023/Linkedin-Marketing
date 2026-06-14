import os
import datetime
from playwright.async_api import async_playwright
from browser.manager import get_authenticated_context, setup_page_stealth, safe_sleep
from browser.vision_fallback import fast_screenshot
from llm.service import answer_application_question
from config import config

MAX_STEPS = 10


async def run_easy_apply(
    job_url: str,
    headless: bool = True,
    save_debug_dir: str = "output/easy_apply",
) -> dict:
    """Navigate to a LinkedIn job URL and complete the Easy Apply flow.

    Returns {"ok": True} on success or
    {"ok": False, "reason": "needs_human | no_easy_apply | timed_out | exception:..."}.
    """
    async with async_playwright() as p:
        context = await get_authenticated_context(p, headless=headless)
        page = await context.new_page()
        await setup_page_stealth(page)

        try:
            await safe_sleep(1.5, 3.0)
            await page.goto(job_url, wait_until="domcontentloaded", timeout=60000)
            try:
                await page.wait_for_selector("main", state="visible", timeout=15000)
            except Exception:
                pass
            await page.wait_for_timeout(2000)

            easy_btn = page.locator(
                "button.jobs-apply-button:has-text('Easy Apply'), "
                "button[aria-label*='Easy Apply' i]"
            ).first
            if await easy_btn.count() == 0:
                await context.browser.close()
                return {"ok": False, "reason": "no_easy_apply"}

            await easy_btn.click()
            await page.wait_for_timeout(1500)

            job_id = job_url.rstrip("/").split("/")[-1] or "unknown"
            debug_dir = os.path.join(save_debug_dir, job_id)
            os.makedirs(debug_dir, exist_ok=True)

            result = await _walk_modal(page, debug_dir)
            await context.browser.close()

            await _log_attempt(job_url, result)
            return result

        except Exception as e:
            try:
                await context.browser.close()
            except Exception:
                pass
            reason = f"exception:{e}"
            await _log_attempt(job_url, {"ok": False, "reason": reason})
            return {"ok": False, "reason": reason}


async def _walk_modal(page, debug_dir: str) -> dict:
    profile = config.APPLICANT_PROFILE

    for step_num in range(MAX_STEPS):
        await page.wait_for_timeout(800)

        modal = page.locator("div[role='dialog'].jobs-easy-apply-modal").first
        if await modal.count() == 0:
            modal = page.locator("div.jobs-easy-apply-modal, div[data-test-modal]").first

        if await modal.count() == 0:
            # Modal closed — either submitted or dismissed
            success_indicator = page.locator(
                "h3:has-text('application was sent'), "
                "h3:has-text('Your application was submitted'), "
                "div[data-test-job-applied-confirmation]"
            ).first
            if await success_indicator.count() > 0:
                return {"ok": True}
            return {"ok": False, "reason": "modal_disappeared"}

        # Save debug screenshot
        try:
            img = await fast_screenshot(page, full_page=False)
            with open(os.path.join(debug_dir, f"step_{step_num}.png"), "wb") as f:
                f.write(img)
        except Exception:
            pass

        # Detect current step header
        header_el = modal.locator("h3, h2").first
        header = (await header_el.inner_text()).strip().lower() if await header_el.count() > 0 else ""

        # Check for a Submit button — means we're on the Review step
        submit_btn = modal.locator(
            "button[aria-label*='Submit application' i], "
            "button:has-text('Submit application')"
        ).first
        if await submit_btn.count() > 0:
            await submit_btn.click()
            await page.wait_for_timeout(2000)
            return {"ok": True}

        # Check for Next / Continue / Review buttons
        next_btn = modal.locator(
            "button[aria-label*='Continue' i], "
            "button[aria-label*='Next' i], "
            "button[aria-label*='Review' i], "
            "button:has-text('Continue to next step'), "
            "button:has-text('Next'), "
            "button:has-text('Review your application')"
        ).first

        # Handle known step types
        if "contact info" in header or "home address" in header:
            await _fill_contact_info(modal, profile)

        elif "resume" in header or "upload" in header:
            await _handle_resume(modal, profile.get("resume_path", ""))

        elif "additional questions" in header or "screening questions" in header or "work authorization" in header:
            needs_human = await _answer_questions(modal)
            if needs_human:
                await _dismiss_modal(page, modal)
                return {"ok": False, "reason": "needs_human"}

        elif "privacy policy" in header:
            # Accept privacy/data consent if checkbox present
            checkbox = modal.locator("input[type='checkbox']").first
            if await checkbox.count() > 0 and not await checkbox.is_checked():
                await checkbox.click()

        else:
            # Unknown step — try to answer any visible questions, else bail
            if await modal.locator("input, select, textarea").count() > 0:
                needs_human = await _answer_questions(modal)
                if needs_human:
                    await _dismiss_modal(page, modal)
                    return {"ok": False, "reason": "needs_human"}

        # Advance to next step
        if await next_btn.count() > 0:
            await next_btn.click()
        else:
            # No Next and no Submit found — stuck
            await _dismiss_modal(page, modal)
            return {"ok": False, "reason": "needs_human"}

    await _dismiss_modal(page, modal if True else None)
    return {"ok": False, "reason": "timed_out"}


async def _fill_contact_info(modal, profile: dict) -> None:
    phone_input = modal.locator("input[id*='phone' i], input[name*='phone' i]").first
    if await phone_input.count() > 0:
        val = await phone_input.input_value()
        if not val and profile.get("phone"):
            await phone_input.fill(profile["phone"])


async def _handle_resume(modal, resume_path: str) -> None:
    if not resume_path or not os.path.exists(resume_path):
        return
    file_input = modal.locator("input[type='file']").first
    if await file_input.count() > 0:
        await file_input.set_input_files(resume_path)
        await modal.page.wait_for_timeout(1500)


async def _answer_questions(modal) -> bool:
    """Try to answer visible form fields using LLM. Returns True if human needed."""
    profile = config.APPLICANT_PROFILE
    needs_human = False

    # Text inputs and textareas
    text_fields = await modal.locator(
        "input[type='text']:not([type='hidden']):not([aria-hidden]), "
        "input[type='number'], textarea"
    ).all()
    for field in text_fields:
        try:
            label = await _get_label(modal, field)
            if not label:
                continue
            current = await field.input_value()
            if current:
                continue
            required = await field.get_attribute("required") is not None
            field_type = "number" if await field.get_attribute("type") == "number" else "text"
            answer = answer_application_question(
                question=label, field_type=field_type, context=profile
            )
            if answer:
                await field.fill(answer)
            elif required:
                needs_human = True
        except Exception:
            pass

    # Select dropdowns
    selects = await modal.locator("select").all()
    for sel in selects:
        try:
            label = await _get_label(modal, sel)
            if not label:
                continue
            options = await sel.locator("option").all()
            option_texts = [await o.inner_text() for o in options]
            option_texts = [t.strip() for t in option_texts if t.strip() and t.strip() != "Select an option"]
            if not option_texts:
                continue
            current = await sel.input_value()
            if current:
                continue
            answer = answer_application_question(
                question=label, field_type="single_select",
                options=option_texts, context=profile,
            )
            if answer in option_texts:
                await sel.select_option(label=answer)
            else:
                needs_human = True
        except Exception:
            pass

    # Radio buttons (yes/no, boolean)
    fieldsets = await modal.locator("fieldset").all()
    for fs in fieldsets:
        try:
            legend = fs.locator("legend").first
            question = (await legend.inner_text()).strip() if await legend.count() > 0 else ""
            if not question:
                continue
            radios = await fs.locator("input[type='radio']").all()
            if any(await r.is_checked() for r in radios):
                continue
            labels = [await fs.locator(f"label[for='{await r.get_attribute('id')}']").first.inner_text()
                      for r in radios]
            answer = answer_application_question(
                question=question, field_type="yes_no",
                options=[l.strip() for l in labels if l.strip()], context=profile,
            )
            for r, lbl in zip(radios, labels):
                if answer.strip().lower() in lbl.strip().lower():
                    await r.click()
                    break
        except Exception:
            pass

    return needs_human


async def _get_label(modal, field) -> str:
    try:
        field_id = await field.get_attribute("id")
        if field_id:
            label_el = modal.locator(f"label[for='{field_id}']").first
            if await label_el.count() > 0:
                return (await label_el.inner_text()).strip()
        aria_label = await field.get_attribute("aria-label")
        if aria_label:
            return aria_label.strip()
        placeholder = await field.get_attribute("placeholder")
        if placeholder:
            return placeholder.strip()
    except Exception:
        pass
    return ""


async def _dismiss_modal(page, modal) -> None:
    try:
        dismiss = page.locator(
            "button[aria-label*='Dismiss' i], button[aria-label*='Close' i]"
        ).first
        if await dismiss.count() > 0:
            await dismiss.click()
            await page.wait_for_timeout(500)
        else:
            await page.keyboard.press("Escape")
    except Exception:
        pass


async def _log_attempt(job_url: str, result: dict) -> None:
    try:
        from db import db
        await db.easy_apply_logs.insert_one({
            "job_url": job_url,
            "ok": result.get("ok", False),
            "reason": result.get("reason"),
            "attempted_at": datetime.datetime.utcnow(),
        })
    except Exception as e:
        print(f"[easy_apply] failed to log attempt: {e}")
