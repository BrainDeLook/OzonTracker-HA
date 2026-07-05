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
import time
from typing import Any
from urllib.parse import quote

from aiohttp import web
from playwright.async_api import (
    Browser,
    BrowserContext,
    Playwright,
    Response,
    async_playwright,
)

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
PORT = int(os.environ.get("PORT", "8080"))
DEBUG = os.environ.get("OZON_DEBUG", "").lower() in ("1", "true", "yes")

# Stealth patches applied to every page before any site script runs, so the
# anti-bot sees a normal browser rather than an automated one.
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU','ru','en-US','en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
window.chrome = window.chrome || { runtime: {} };
const _q = window.navigator.permissions && window.navigator.permissions.query;
if (_q) {
  window.navigator.permissions.query = (p) => (
    p && p.name === 'notifications'
      ? Promise.resolve({state: Notification.permission})
      : _q(p)
  );
}
"""


class TrackingBrowser:
    """Owns a single Chromium context and serves tracking lookups from it."""

    def __init__(self) -> None:
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._ctx: BrowserContext | None = None
        self._lock = asyncio.Lock()

    @property
    def ready(self) -> bool:
        return self._ctx is not None

    async def start(self) -> None:
        self._pw = await async_playwright().start()
        # A headless browser is trivially detected by anti-bots. When a display
        # is available (run.sh starts Xvfb) we launch a *headed* Chromium, which
        # is far harder to fingerprint. If a headed launch fails we fall back to
        # headless so the service still works. OZON_HEADLESS=1 forces headless.
        force = os.environ.get("OZON_HEADLESS")
        prefer_headless = force == "1" if force else not os.environ.get("DISPLAY")
        try:
            await self._launch(prefer_headless)
        except Exception as err:  # noqa: BLE001
            if prefer_headless:
                raise
            _LOGGER.warning(
                "Headed Chromium launch failed (%s); retrying headless", err
            )
            await self._launch(True)

    async def _launch(self, headless: bool) -> None:
        assert self._pw is not None
        _LOGGER.info(
            "Launching Chromium (headless=%s, display=%s)",
            headless,
            os.environ.get("DISPLAY") or "none",
        )
        self._browser = await self._pw.chromium.launch(
            headless=headless,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--window-size=1280,800",
            ],
        )
        self._ctx = await self._browser.new_context(
            user_agent=USER_AGENT,
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            viewport={"width": 1280, "height": 800},
            extra_http_headers={"Accept-Language": "ru,en;q=0.9"},
        )
        await self._ctx.add_init_script(STEALTH_JS)
        self._ctx.set_default_navigation_timeout(NAV_TIMEOUT)
        _LOGGER.info("Chromium context ready (headless=%s)", headless)

    async def close(self) -> None:
        for closer in (
            getattr(self._ctx, "close", None),
            getattr(self._browser, "close", None),
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
            timeout=NAV_TIMEOUT,
        )
        return resp.status, await resp.text()

    async def _settle(self, page) -> None:
        """Wait for the page to go quiet so challenge JS can run."""
        try:
            await page.wait_for_load_state("networkidle", timeout=12000)
        except Exception:  # noqa: BLE001
            await asyncio.sleep(3)

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
        must run (and set the session cookie) before the real app loads. So we
        warm up on the site root, let the challenge settle, then re-navigate to
        the tracking page through the challenge, capturing the BFF response the
        app makes (or calling the BFF ourselves once cookies are valid).
        """
        assert self._ctx is not None
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
            # 1) Warm up on the root so the challenge script runs and sets cookies.
            try:
                await page.goto(f"{BASE}/", wait_until="domcontentloaded")
                await self._settle(page)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Root warm-up failed: %s", err)

            # 2) Load the tracking page, re-navigating through the challenge.
            deadline = time.monotonic() + NAV_TIMEOUT / 1000
            while time.monotonic() < deadline and "body" not in captured:
                attempts += 1
                try:
                    await page.goto(
                        PAGE_URL.format(track=track), wait_until="domcontentloaded"
                    )
                    await self._settle(page)
                except Exception as err:  # noqa: BLE001
                    _LOGGER.debug("Nav attempt %s failed: %s", attempts, err)

                if "body" in captured:
                    break
                if not await self._is_challenge_page(page):
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
                    # Give the app a moment to fire its own BFF call.
                    await asyncio.sleep(2)
                else:
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
