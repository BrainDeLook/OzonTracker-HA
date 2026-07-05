"""Ozon tracking proxy backed by a real headless Chromium.

Ozon guards ``tracking.ozon.ru`` with a JavaScript proof-of-work anti-bot
challenge (``fab_ichlg``). The token it produces lives in a cookie that can
only be obtained by executing the challenge JS in a real browser, so a plain
HTTP client is always answered with HTTP 403.

This tiny service runs Chromium via Playwright, lets the page solve the
challenge once, keeps the resulting session (cookies) alive in a persistent
browser context, and exposes the tracking data over a simple local HTTP API::

    GET /track/{tracking_number}  -> raw Ozon BFF JSON (HTTP 200)
    GET /healthz                  -> {"status": "ok"}

The Home Assistant integration points at this service instead of hitting
Ozon directly, so the anti-bot cookie is refreshed automatically and never
has to be pasted by hand.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from typing import Any
from urllib.parse import quote

from aiohttp import web

# Playwright types are always importable (camoufox depends on Playwright).
from playwright.async_api import BrowserContext, Playwright, Response

# camoufox: a Firefox anti-detect browser that spoofs the full fingerprint —
# the default engine, since Chromium (even patched) failed Ozon's challenge
# under a GPU-less virtual display.
try:
    from camoufox.async_api import AsyncCamoufox

    _HAS_CAMOUFOX = True
except Exception:  # noqa: BLE001  # pragma: no cover
    _HAS_CAMOUFOX = False

# Chromium fallback engine: prefer patchright (undetected), else plain Playwright.
try:
    from patchright.async_api import async_playwright as chromium_playwright

    PATCHED = True
except ImportError:  # pragma: no cover
    from playwright.async_api import async_playwright as chromium_playwright

    PATCHED = False

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
_LOGGER = logging.getLogger("ozon-tracker-proxy")

BASE = "https://tracking.ozon.ru"
BFF_URL = BASE + "/p-api/ozon-track-bff/tracking/{track}"
PAGE_URL = BASE + "/?track={track}"

USER_AGENT = os.environ.get("OZON_USER_AGENT") or (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)
APP_HEADERS = {
    "x-o3-app-name": "tpl-ui-ozon-track",
    "x-o3-app-version": os.environ.get("OZON_APP_VERSION") or "release/TPLAPI-5269",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru,en;q=0.9",
}

NAV_TIMEOUT = int(os.environ.get("OZON_NAV_TIMEOUT_MS") or "60000")
# Per-navigation cap, kept short so a single busy page can't eat the whole
# solve budget (the challenge page keeps its network active for a long time).
PER_NAV_TIMEOUT = min(45000, NAV_TIMEOUT)
PORT = int(os.environ.get("PORT", "8080"))
DEBUG = os.environ.get("OZON_DEBUG", "").lower() in ("1", "true", "yes")
# Browser engine: "camoufox" (Firefox anti-detect, default) or "chromium".
ENGINE = (os.environ.get("OZON_ENGINE") or "camoufox").lower()

# Minimal stealth patches, only used on the vanilla-Playwright fallback.
# patchright already handles these (and the CDP leaks) itself, so we must NOT
# add them there — a double patch can itself become a detection signal.
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU','ru','en-US','en']});
window.chrome = window.chrome || { runtime: {} };
"""


def _profile_dir() -> str:
    """A writable directory to persist the browser profile (and its cookies).

    In the Home Assistant add-on /data persists across restarts, so a solved
    anti-bot session survives; otherwise fall back to /tmp.
    """
    for candidate in (os.environ.get("OZON_PROFILE_DIR"), "/data/ozon-profile", "/tmp/ozon-profile"):
        if not candidate:
            continue
        try:
            os.makedirs(candidate, exist_ok=True)
            return candidate
        except Exception:  # noqa: BLE001
            continue
    return "/tmp/ozon-profile"


class TrackingBrowser:
    """Owns a single persistent Chromium context and serves lookups from it."""

    def __init__(self) -> None:
        self._pw: Playwright | None = None
        self._cam: Any | None = None
        self._ctx: BrowserContext | None = None
        self._lock = asyncio.Lock()

    @property
    def ready(self) -> bool:
        return self._ctx is not None

    async def start(self) -> None:
        if ENGINE == "camoufox" and _HAS_CAMOUFOX:
            await self._start_camoufox()
        else:
            if ENGINE == "camoufox":
                _LOGGER.warning("camoufox not available; falling back to Chromium")
            await self._start_chromium()

    async def _start_camoufox(self) -> None:
        # headless="virtual" runs a *headed* Firefox under Camoufox's own Xvfb
        # (best for a headless server). Honour an external DISPLAY / OZON_HEADLESS.
        force = os.environ.get("OZON_HEADLESS")
        if force == "1":
            headless: Any = True
        elif os.environ.get("DISPLAY"):
            headless = False
        else:
            headless = "virtual"
        _LOGGER.info("Launching Camoufox (headless=%s)", headless)
        self._cam = AsyncCamoufox(
            headless=headless,
            persistent_context=True,
            user_data_dir=_profile_dir(),
            os="windows",
            locale="ru-RU",
            humanize=True,
            i_know_what_im_doing=True,
        )
        self._ctx = await self._cam.__aenter__()
        self._ctx.set_default_navigation_timeout(NAV_TIMEOUT)
        _LOGGER.info("Camoufox context ready (headless=%s)", headless)

    async def _start_chromium(self) -> None:
        self._pw = await chromium_playwright().start()
        force = os.environ.get("OZON_HEADLESS")
        prefer_headless = force == "1" if force else not os.environ.get("DISPLAY")
        try:
            await self._launch_chromium(prefer_headless)
        except Exception as err:  # noqa: BLE001
            if prefer_headless:
                raise
            _LOGGER.warning("Headed launch failed (%s); retrying headless", err)
            await self._launch_chromium(True)

    async def _launch_chromium(self, headless: bool) -> None:
        assert self._pw is not None
        _LOGGER.info(
            "Launching Chromium (patchright=%s, headless=%s, display=%s)",
            PATCHED,
            headless,
            os.environ.get("DISPLAY") or "none",
        )
        self._ctx = await self._pw.chromium.launch_persistent_context(
            user_data_dir=_profile_dir(),
            headless=headless,
            user_agent=USER_AGENT,
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            no_viewport=True,
            extra_http_headers={"Accept-Language": "ru,en;q=0.9"},
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--enable-unsafe-swiftshader",
            ],
        )
        if not PATCHED:
            await self._ctx.add_init_script(STEALTH_JS)
        self._ctx.set_default_navigation_timeout(NAV_TIMEOUT)
        _LOGGER.info("Chromium context ready (patchright=%s, headless=%s)", PATCHED, headless)

    async def close(self) -> None:
        if self._cam is not None:
            try:
                await self._cam.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
            self._cam = None
            self._ctx = None
            return
        for closer in (
            getattr(self._ctx, "close", None),
            getattr(self._pw, "stop", None),
        ):
            if closer is None:
                continue
            try:
                await closer()
            except Exception:  # noqa: BLE001
                pass

    async def _api_request(self, track: str) -> tuple[int, str]:
        """Call the BFF endpoint reusing the context cookies (no navigation)."""
        assert self._ctx is not None
        resp = await self._ctx.request.get(
            BFF_URL.format(track=quote(track, safe="")),
            headers={**APP_HEADERS, "Referer": PAGE_URL.format(track=track)},
            timeout=20000,
        )
        return resp.status, await resp.text()

    async def _goto(self, page, url: str) -> None:
        """Navigate with a bounded timeout so one busy page can't hang us."""
        await page.goto(url, wait_until="domcontentloaded", timeout=PER_NAV_TIMEOUT)

    async def _settle(self, page) -> None:
        """Wait for the page to go quiet so challenge JS can run."""
        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:  # noqa: BLE001
            await asyncio.sleep(2)

    @staticmethod
    async def _humanize(page) -> None:
        """A few human-like signals: mouse movement, scroll, a short dwell."""
        try:
            for x, y in ((180, 240), (520, 360), (760, 520)):
                await page.mouse.move(x, y, steps=6)
                await asyncio.sleep(random.uniform(0.1, 0.35))
            await page.mouse.wheel(0, random.randint(300, 700))
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(random.uniform(0.6, 1.4))

    @staticmethod
    async def _is_challenge_page(page) -> bool:
        """Heuristic: are we looking at the anti-bot challenge page?"""
        try:
            if await page.query_selector(".challenge-data, [class*=challenge]"):
                return True
        except Exception:  # noqa: BLE001
            pass
        try:
            title = (await page.title()) or ""
        except Exception:  # noqa: BLE001
            title = ""
        low = title.lower()
        return any(m in low for m in ("нет соединения", "challenge", "проверка"))

    async def _solve_via_page(self, track: str) -> tuple[int, str] | None:
        """Solve the anti-bot challenge with the browser, then read the BFF.

        The tracking URL itself returns the anti-bot challenge page, whose JS
        must run (and set the session cookie) before the real app loads. We
        visit the site root like a human (mouse/scroll/dwell) so the challenge
        clears, then navigate to the tracking page and read the app's BFF call.
        Every step is logged and time-bounded so we never hang silently.
        """
        assert self._ctx is not None
        _LOGGER.info("Solving via browser for %s (budget %ss)", track, NAV_TIMEOUT // 1000)
        page = await self._ctx.new_page()
        captured: dict[str, Any] = {}
        seen: list[int] = []
        counters = {"responses": 0, "challenge_assets": 0}

        async def on_response(resp: Response) -> None:
            counters["responses"] += 1
            if "abt-challenge" in resp.url or "/challenge" in resp.url:
                counters["challenge_assets"] += 1
            if "ozon-track-bff/tracking" in resp.url:
                seen.append(resp.status)
                if resp.status == 200 and "body" not in captured:
                    try:
                        captured["body"] = await resp.text()
                    except Exception:  # noqa: BLE001
                        pass

        page.on("response", on_response)
        attempts = 0
        try:
            # 1) Warm up on the root like a human so the challenge clears.
            try:
                await self._goto(page, f"{BASE}/")
                await self._settle(page)
                await self._humanize(page)
                _LOGGER.info(
                    "Warm-up done for %s: url=%s challenge=%s",
                    track, page.url, await self._is_challenge_page(page),
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.info("Root warm-up failed for %s: %s", track, err)

            # 2) Load the tracking page, re-navigating through the challenge.
            deadline = time.monotonic() + NAV_TIMEOUT / 1000
            while time.monotonic() < deadline and "body" not in captured:
                attempts += 1
                try:
                    await self._goto(page, PAGE_URL.format(track=track))
                    await self._settle(page)
                    await self._humanize(page)
                except Exception as err:  # noqa: BLE001
                    _LOGGER.info("Nav attempt %s for %s failed: %s", attempts, track, err)

                if "body" in captured:
                    break
                is_challenge = await self._is_challenge_page(page)
                _LOGGER.info(
                    "Attempt %s for %s: url=%s challenge=%s bff=%s",
                    attempts, track, page.url, is_challenge, seen or "none",
                )
                if not is_challenge:
                    # Challenge cleared: cookies are valid, fetch it ourselves.
                    try:
                        status, body = await self._api_request(track)
                        if status == 200:
                            captured["body"] = body
                            break
                        if status == 404:
                            return 404, body
                    except Exception:  # noqa: BLE001
                        pass
                await asyncio.sleep(2)

            _LOGGER.info(
                "Solve %s: final_url=%s bff=%s responses=%s challenge_assets=%s "
                "attempts=%s solved=%s",
                track,
                page.url,
                seen or "none",
                counters["responses"],
                counters["challenge_assets"],
                attempts,
                "body" in captured,
            )
            if "body" not in captured and DEBUG:
                try:
                    title = await page.title()
                    html = await page.content()
                    _LOGGER.info("DEBUG %s title=%r page[:3000]=%r", track, title, html[:3000])
                except Exception:  # noqa: BLE001
                    pass
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Page navigation for %s failed: %s", track, err)
        finally:
            await page.close()
        if "body" in captured:
            return 200, captured["body"]
        return None

    async def fetch(self, track: str) -> tuple[int, str]:
        """Return (status, body) for a tracking number, solving challenges."""
        async with self._lock:
            # 1) Fast path: reuse the existing session cookies directly.
            try:
                status, body = await self._api_request(track)
                if status == 200:
                    return 200, body
                if status == 404:
                    return 404, body
                _LOGGER.info("Direct request for %s got %s; solving via page", track, status)
            except Exception as err:  # noqa: BLE001
                _LOGGER.info("Direct request for %s failed (%s); solving via page", track, err)

            # 2) Solve the challenge by loading the real page.
            result = await self._solve_via_page(track)
            if result is not None:
                return result
            return 502, '{"error": "anti-bot challenge not solved in browser"}'


browser = TrackingBrowser()


async def handle_track(request: web.Request) -> web.Response:
    track = request.match_info["track"].strip()
    if not track:
        return web.json_response({"error": "empty tracking number"}, status=400)
    if not browser.ready:
        return web.json_response(
            {"error": "browser not started; check the add-on log"}, status=503
        )
    status, body = await browser.fetch(track)
    if status == 200:
        return web.Response(
            body=body, status=200, content_type="application/json"
        )
    if status == 404:
        return web.json_response({"error": "not found"}, status=404)
    _LOGGER.warning("Tracking %s -> upstream status %s", track, status)
    return web.json_response(
        {"error": f"upstream status {status}"}, status=502
    )


async def handle_health(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def on_startup(_app: web.Application) -> None:
    # Never let a browser launch failure prevent the HTTP server from starting:
    # /healthz stays up and /track returns a clear 503 so the problem is visible.
    try:
        await browser.start()
    except Exception:  # noqa: BLE001
        _LOGGER.exception(
            "Browser failed to start; /track will return 503 until this is fixed"
        )


async def on_cleanup(_app: web.Application) -> None:
    await browser.close()


def make_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/healthz", handle_health)
    app.router.add_get("/track/{track}", handle_track)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    _LOGGER.info("Starting Ozon tracking proxy on port %s", PORT)
    web.run_app(make_app(), port=PORT)
