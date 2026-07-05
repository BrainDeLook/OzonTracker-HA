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

    async def start(self) -> None:
        self._pw = await async_playwright().start()
        # A headless browser is trivially detected by anti-bots. When a display
        # is available (run.sh starts us under xvfb) we launch a *headed*
        # Chromium, which is far harder to fingerprint. OZON_HEADLESS=1 forces
        # headless if you cannot provide a display.
        force = os.environ.get("OZON_HEADLESS")
        headless = force == "1" if force else not os.environ.get("DISPLAY")
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
        _LOGGER.info(
            "Chromium context ready (headless=%s, display=%s)",
            headless,
            os.environ.get("DISPLAY") or "none",
        )

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

    async def _solve_via_page(self, track: str) -> tuple[int, str] | None:
        """Load the real page so its JS solves the challenge, then read the BFF.

        Two ways to obtain the data are raced against the timeout:
        1. capture a 200 BFF response the page itself makes, and
        2. once challenge cookies exist in the context, call the BFF ourselves.
        """
        assert self._ctx is not None
        page = await self._ctx.new_page()
        captured: dict[str, Any] = {}
        seen_statuses: list[int] = []

        async def on_response(resp: Response) -> None:
            if "ozon-track-bff/tracking" in resp.url:
                seen_statuses.append(resp.status)
                if resp.status == 200 and "body" not in captured:
                    try:
                        captured["body"] = await resp.text()
                    except Exception:  # noqa: BLE001
                        pass

        page.on("response", on_response)
        try:
            await page.goto(
                PAGE_URL.format(track=track), wait_until="domcontentloaded"
            )
            deadline = time.monotonic() + NAV_TIMEOUT / 1000
            while time.monotonic() < deadline:
                if "body" in captured:
                    break
                # The page may have solved the challenge (cookies set) without
                # yet firing the BFF call for us to capture -> ask ourselves.
                try:
                    status, body = await self._api_request(track)
                    if status == 200:
                        captured["body"] = body
                        break
                    if status == 404:
                        return 404, body
                except Exception:  # noqa: BLE001
                    pass
                await asyncio.sleep(1.5)

            _LOGGER.info(
                "Solve %s: final_url=%s bff_statuses=%s solved=%s",
                track,
                page.url,
                seen_statuses or "none",
                "body" in captured,
            )
            if "body" not in captured and DEBUG:
                try:
                    html = await page.content()
                    _LOGGER.info("DEBUG %s page[:1000]=%r", track, html[:1000])
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
    await browser.start()


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
