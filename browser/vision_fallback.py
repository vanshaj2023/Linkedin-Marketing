import base64
import json
import os
from typing import Optional, Tuple

from openai import OpenAI

from config import config


def _to_css_coords(page, image_png: bytes, x_px: int, y_px: int) -> Tuple[float, float]:
    """Scale image-pixel coords (what vision saw) into CSS-pixel coords.

    CDP's captureScreenshot returns the image at device pixels (DPR-scaled),
    so on a 1366x768 viewport with DPR=1.25 the PNG is 1708x960. But
    page.mouse.click() expects CSS-pixel coords matching the viewport.
    We compute the scale from actual image size to actual viewport size.
    """
    try:
        import io
        from PIL import Image
        img_w, img_h = Image.open(io.BytesIO(image_png)).size
    except Exception:
        return float(x_px), float(y_px)
    vp = page.viewport_size or {"width": img_w, "height": img_h}
    sx = vp["width"] / img_w if img_w else 1.0
    sy = vp["height"] / img_h if img_h else 1.0
    return x_px * sx, y_px * sy


def _save_annotated(image_png: bytes, pick: dict, path: str) -> None:
    """Save the screenshot with a red dot + crosshair at the click point."""
    try:
        import io
        from PIL import Image, ImageDraw
        img = Image.open(io.BytesIO(image_png)).convert("RGB")
        draw = ImageDraw.Draw(img)
        x, y = pick["x"], pick["y"]
        r = 18
        draw.ellipse((x - r, y - r, x + r, y + r), outline=(255, 0, 0), width=4)
        draw.line((x - r * 2, y, x + r * 2, y), fill=(255, 0, 0), width=2)
        draw.line((x, y - r * 2, x, y + r * 2), fill=(255, 0, 0), width=2)
        label = f"{pick['kind']} ({pick['confidence']:.2f})"
        draw.text((x + r + 4, y - r), label, fill=(255, 0, 0))
        img.save(path)
    except Exception as e:
        print(f"[vision_fallback] could not save annotated screenshot: {e}")


async def fast_screenshot(page, full_page: bool = False) -> bytes:
    """Take a PNG screenshot via CDP, bypassing Playwright's font-load wait.

    LinkedIn occasionally serves a custom font whose `document.fonts.ready`
    promise never resolves, which deadlocks Playwright's screenshot() until
    its 30s timeout. CDP's Page.captureScreenshot has no such wait.
    """
    try:
        cdp = await page.context.new_cdp_session(page)
        params = {"format": "png"}
        if full_page:
            params["captureBeyondViewport"] = True
        result = await cdp.send("Page.captureScreenshot", params)
        try:
            await cdp.detach()
        except Exception:
            pass
        return base64.b64decode(result["data"])
    except Exception:
        return await page.screenshot(type="png", full_page=full_page, timeout=8000)


_client: Optional[OpenAI] = None


def _get_client() -> Optional[OpenAI]:
    global _client
    if _client is not None:
        return _client
    api_key = config.OPENAI_API_KEY or os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return None
    _client = OpenAI(api_key=api_key)
    return _client


_SYSTEM_PROMPT = (
    "You are a UI navigation assistant for LinkedIn profile pages. You are shown "
    "a viewport screenshot and the profile owner's name. Find the button that "
    "initiates a connection request for THIS profile owner.\n\n"
    "LINKEDIN PROFILE PAGE LAYOUT (memorise this):\n"
    "- The page has TWO columns.\n"
    "- LEFT COLUMN (wide, ~65-75% of width): the profile owner's main card. "
    "It contains the large circular profile photo, the owner's name in big text, "
    "their headline, location, and a row of action buttons (Connect, Message, "
    "Follow, More).\n"
    "- RIGHT COLUMN (narrow, ~25-30% of width, on the right edge): sidebars "
    "titled 'People you may know', 'People also viewed', 'More profiles for you', "
    "'Other similar profiles', or job ads. These contain SMALL cards each with "
    "their own tiny Connect / Follow buttons. THESE ARE TRAPS.\n\n"
    "RULES:\n"
    "1. ONLY ever return coordinates inside the LEFT COLUMN. If a Connect button "
    "is on the right side of the image (right ~30% horizontally), it belongs to "
    "a recommendation card — DO NOT pick it. Return null instead.\n"
    "2. The correct Connect button is on the SAME ROW as Message, just below the "
    "owner's headline / location / 'Open to work' badge.\n"
    "3. If Connect is hidden behind a 'More' / 'More actions' overflow button in "
    "the LEFT COLUMN top card, return that More button's coordinates with "
    "kind='more'. The caller will open the menu and re-ask.\n"
    "4. If a dropdown menu is currently open and one item says 'Connect', return "
    "its coordinates with kind='connect'.\n"
    "5. Coordinates are pixel positions (top-left origin) in the screenshot you "
    "were shown.\n"
    "6. Reply with strict JSON only — no markdown, no commentary.\n\n"
    "Schema:\n"
    "{\"x\": <int|null>, \"y\": <int|null>, "
    "\"kind\": <\"connect\"|\"more\"|null>, "
    "\"confidence\": <float 0..1>, "
    "\"reason\": <string explaining which region you picked and why>}\n\n"
    "If no Connect/More button exists in the LEFT COLUMN, return: "
    "{\"x\": null, \"y\": null, \"kind\": null, \"confidence\": 0.0, "
    "\"reason\": \"...\"}."
)


def _ask_vision(image_png: bytes, profile_name: str, menu_open: bool) -> Optional[dict]:
    client = _get_client()
    if not client:
        print("[vision_fallback] OPENAI_API_KEY missing; skipping vision fallback.")
        return None
    try:
        b64 = base64.b64encode(image_png).decode()
        user_hint = (
            f"Profile owner: '{profile_name}'. "
            + (
                "A dropdown menu is currently open; pick the menu item that says 'Connect'."
                if menu_open
                else "Find the Connect button (or the top-card 'More' button if Connect is hidden)."
            )
        )
        resp = client.chat.completions.create(
            model=config.OPENAI_VISION_MODEL,
            temperature=0.0,
            max_tokens=300,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_hint},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{b64}",
                                "detail": "high",
                            },
                        },
                    ],
                },
            ],
        )
        raw = resp.choices[0].message.content
        data = json.loads(raw)
        if data.get("x") is None or data.get("y") is None:
            print(f"[vision_fallback] LLM saw no target: {data.get('reason')}")
            return None
        return {
            "x": int(data["x"]),
            "y": int(data["y"]),
            "kind": data.get("kind", "connect"),
            "confidence": float(data.get("confidence", 0.0)),
            "reason": data.get("reason", ""),
        }
    except Exception as e:
        print(f"[vision_fallback] Vision call failed: {e}")
        return None


async def vision_assisted_connect_click(
    page, profile_name: str, save_debug_dir: Optional[str] = None
) -> Tuple[bool, str]:
    """Use a vision LLM to locate and click the Connect (or More→Connect) button.

    Returns (success, reason). Success means a Connect-related click was issued;
    the caller is still responsible for handling the modal that follows.
    """
    if not profile_name:
        profile_name = "(unknown — pick the Connect button in the top profile card only)"

    await page.evaluate("window.scrollTo(0, 0)")
    await page.wait_for_timeout(600)

    shot1 = await fast_screenshot(page, full_page=False)
    if save_debug_dir:
        try:
            os.makedirs(save_debug_dir, exist_ok=True)
            with open(os.path.join(save_debug_dir, "vision_step1.png"), "wb") as f:
                f.write(shot1)
        except Exception:
            pass

    pick = _ask_vision(shot1, profile_name, menu_open=False)
    if not pick:
        return False, "vision_no_target"

    print(
        f"[vision_fallback] step1 pick: x={pick['x']}, y={pick['y']}, "
        f"kind={pick['kind']}, conf={pick['confidence']:.2f}, "
        f"reason={pick['reason']!r}"
    )
    if save_debug_dir:
        _save_annotated(shot1, pick, os.path.join(save_debug_dir, "vision_step1_clicked.png"))

    cx, cy = _to_css_coords(page, shot1, pick["x"], pick["y"])
    print(f"[vision_fallback] step1 click (css): x={cx:.0f}, y={cy:.0f}")
    await page.mouse.click(cx, cy)
    await page.wait_for_timeout(900)

    if pick["kind"] == "connect":
        return True, f"vision_connect (conf={pick['confidence']:.2f})"

    if pick["kind"] == "more":
        try:
            await page.wait_for_selector("div[role='menu']", state="visible", timeout=4000)
        except Exception:
            pass
        await page.wait_for_timeout(400)

        shot2 = await fast_screenshot(page, full_page=False)
        if save_debug_dir:
            try:
                with open(os.path.join(save_debug_dir, "vision_step2.png"), "wb") as f:
                    f.write(shot2)
            except Exception:
                pass

        pick2 = _ask_vision(shot2, profile_name, menu_open=True)
        if not pick2 or pick2["kind"] != "connect":
            await page.keyboard.press("Escape")
            return False, "vision_no_connect_in_menu"

        print(
            f"[vision_fallback] step2 pick: x={pick2['x']}, y={pick2['y']}, "
            f"kind={pick2['kind']}, conf={pick2['confidence']:.2f}, "
            f"reason={pick2['reason']!r}"
        )
        if save_debug_dir:
            _save_annotated(shot2, pick2, os.path.join(save_debug_dir, "vision_step2_clicked.png"))

        cx2, cy2 = _to_css_coords(page, shot2, pick2["x"], pick2["y"])
        print(f"[vision_fallback] step2 click (css): x={cx2:.0f}, y={cy2:.0f}")
        await page.mouse.click(cx2, cy2)
        await page.wait_for_timeout(900)
        return True, f"vision_more_then_connect (conf={pick2['confidence']:.2f})"

    return False, f"vision_unknown_kind:{pick['kind']}"
