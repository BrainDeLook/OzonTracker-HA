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
# How long a solved session is trusted before we re-verify via a full page
# navigation. A direct context request is attempted first regardless.
PORT = int(os.environ.get("PORT", "8080"))


class TrackingBrowser:
    """Owns a single Chromium context and serves tracking lookups from it."""

    def __init__(self) -> None:
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._ctx: BrowserContext | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        self._ctx = await self._browser.new_context(
            user_agent=USER_AGENT,
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            viewport={"width": 1280, "height": 800},
        )
        self._ctx.set_default_navigation_timeout(NAV_TIMEOUT)
        _LOGGER.info("Chromium context ready")

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
        """Navigate the real page so its JS solves the challenge; capture BFF."""
        assert self._ctx is not None
        page = await self._ctx.new_page()
        captured: dict[str, Any] = {}

        async def on_response(resp: Response) -> None:
            if "ozon-track-bff/tracking" in resp.url and resp.status == 200:
                try:
                    captured["body"] = await resp.text()
                except Exception:  # noqa: BLE001
                    pass

        page.on("response", on_response)
        try:
            await page.goto(
                PAGE_URL.format(track=track), wait_until="domcontentloaded"
            )
            # Give the challenge + app time to run and fire the BFF call.
            for _ in range(30):
                if "body" in captured:
                    break
                await asyncio.sleep(0.5)
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

            # 2) Solve the challenge by loading the real page, capture the BFF.
            captured = await self._solve_via_page(track)
            if captured is not None:
                return captured

            # 3) Retry the direct request now that challenge cookies are set.
            try:
                status, body = await self._api_request(track)
                return status, body
            except Exception as err:  # noqa: BLE001
                return 502, f'{{"error": "browser fetch failed: {err}"}}'


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
