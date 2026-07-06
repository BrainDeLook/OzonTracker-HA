"""Client for the public tracking.ozon.ru parcel tracking service.

The tracking web page loads its data from
``GET https://tracking.ozon.ru/p-api/ozon-track-bff/tracking/{tracking_number}``
which returns JSON like::

    {
        "deliveryDateBegin": "2026-07-16T08:30:00+00:00",
        "deliveryDateEnd": "2026-07-24T08:30:00+00:00",
        "deliveryDatePeriodChangedMoment": "2026-06-29T20:20:22+00:00",
        "deliveryType": "Courier",
        "deliveryPostponementType": "Unknown",
        "items": [
            {"event": "Created", "moment": "2026-06-29T20:20:37+00:00"},
            {"event": "TransferringToDelivery", "moment": "..."},
            {"event": "WayToCity", "moment": "..."}
        ]
    }

There is no "current status" field: the newest entry of ``items`` is the
current state, and ``event`` is a machine code that we map to human readable
Russian text. Unknown codes fall back to a de-camel-cased version of the code
so new Ozon statuses still render something sensible.

Because the endpoint is not officially documented, a generic schema-tolerant
parser and an HTML-page fallback are kept as safety nets for future changes.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import aiohttp

try:
    # curl_cffi impersonates a real Chrome TLS/JA3/HTTP2 fingerprint, which
    # is what defeats Ozon's anti-bot "challenge" gate. It is a soft
    # dependency: if it can not be imported we transparently fall back to
    # aiohttp (which usually gets a 403 challenge).
    from curl_cffi.requests import AsyncSession as _CurlAsyncSession

    HAS_CURL_CFFI = True
except ImportError:  # pragma: no cover - depends on install platform
    _CurlAsyncSession = None
    HAS_CURL_CFFI = False

_LOGGER = logging.getLogger(__name__)

API_ENDPOINT = "https://tracking.ozon.ru/p-api/ozon-track-bff/tracking/{tracking_number}"
PAGE_URL = "https://tracking.ozon.ru/"

TIMEOUT_SECONDS = 30
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)

# curl_cffi browser profile to impersonate. "chrome" tracks a recent Chrome.
CURL_IMPERSONATE = "chrome"

# Markers that identify Ozon's anti-bot challenge response.
CHALLENGE_MARKERS = ("challengeURL", "challenge.html", "incidentId")

# Maximum number of history events kept per package (long international routes
# can have 20+ checkpoints).
EVENTS_LIMIT = 60

# --- track365.ru aggregator source ---------------------------------------
# track365.ru shows Ozon (and many other) parcels with rich, human-readable
# statuses and, importantly, without a JS anti-bot challenge — so it can be
# queried with a plain GET. Its /TRACK_SERVER.php endpoint expects an obfuscated
# `fp` parameter that is simply base64 of the char codes (each XOR 6) of
# PREFIX + User-Agent + tracking number (reverse-engineered and verified
# byte-for-byte against a captured request).
TRACK365_ENDPOINT = "https://track365.ru/TRACK_SERVER.php"
TRACK365_PAGE = "https://track365.ru/?track={tracking_number}"
TRACK365_FP_PREFIX = "bbbcbb``c"
TRACK365_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)
# Overall statuses and per-event place codes / text that mean "delivered".
TRACK365_DELIVERED_STATUSES = {"delivered", "received", "pickup", "delivered_to_pickup"}
TRACK365_DELIVERED_PLACES = {
    "TYP_SUCCESS",
    "TYP_DELIVERED",
    "TYP_PICKED_UP",
    "TYP_PICKUP",
    "TYP_HANDED",
}

# Headers common to every request. When curl_cffi impersonates a browser it
# already sets User-Agent / sec-ch-ua* and the TLS fingerprint, so those are
# kept separate (FINGERPRINT_HEADERS) and only added on the aiohttp path.
COMMON_HEADERS = {
    "Accept-Language": "ru,en;q=0.9",
}

FINGERPRINT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
    ),
    "sec-ch-ua": '"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

# Application headers the Ozon tracking web app (Nuxt) sends on its API
# (XHR) calls. Their absence is what triggers the anti-bot 403 challenge,
# so these are the important part of the fix. Captured from a real session;
# update the version string if Ozon starts rejecting it.
APP_HEADERS = {
    "x-o3-app-name": "tpl-ui-ozon-track",
    "x-o3-app-version": "release/TPLAPI-5269",
}

# Human readable (Russian) names for the BFF event codes. Unknown codes are
# rendered by _humanize_code() instead.
EVENT_NAMES = {
    "Created": "Заказ создан",
    "AwaitingPackaging": "Ожидает сборки",
    "Packaging": "Собирается",
    "Packed": "Заказ собран",
    "TransferringToDelivery": "Передаётся в доставку",
    "TransferredToDelivery": "Передано в доставку",
    "WayToCity": "Едет в ваш город",
    "ArrivedToCity": "Прибыло в ваш город",
    "ArrivedInCity": "Прибыло в ваш город",
    "OnSortingCenter": "На сортировочном центре",
    "SortingCenter": "На сортировочном центре",
    "WayToPickPoint": "Едет в пункт выдачи",
    "ArrivedToPickPoint": "Прибыло в пункт выдачи",
    "DeliveredToPickPoint": "Прибыло в пункт выдачи",
    "ReadyForPickup": "Готово к выдаче",
    "CourierInTransit": "Курьер в пути",
    "Delivering": "Доставляется",
    "Delivered": "Доставлено",
    "DeliveredToClient": "Вручено",
    "PickedUpByClient": "Получено",
    "Cancelled": "Отменено",
    "Canceled": "Отменено",
    "ClientRefused": "Покупатель отказался",
    "Returning": "Возвращается продавцу",
    "Returned": "Возвращено продавцу",
}

DELIVERY_TYPE_NAMES = {
    "Courier": "Курьер",
    "PickPoint": "Пункт выдачи",
    "PickupPoint": "Пункт выдачи",
    "Postamat": "Постамат",
    "Post": "Почта",
}

DELIVERED_CODES = {
    "Delivered",
    "DeliveredToClient",
    "PickedUpByClient",
    "Received",
}

# --- generic fallback parser configuration -------------------------------

TEXT_KEYS = (
    "statusText",
    "status_text",
    "statusName",
    "status_name",
    "name",
    "title",
    "status",
    "event",
    "text",
    "description",
    "message",
)

TIME_KEYS = (
    "date",
    "datetime",
    "dateTime",
    "time",
    "moment",
    "timestamp",
    "created_at",
    "createdAt",
    "updated_at",
    "updatedAt",
    "eventDt",
    "event_dt",
)

STATUS_KEYS = (
    "currentStatusText",
    "current_status_text",
    "statusText",
    "status_text",
    "currentStatusName",
    "currentStatus",
    "current_status",
    "statusName",
    "status_name",
    "trackingStatus",
    "tracking_status",
    "status",
    "state",
)

DELIVERED_MARKERS = (
    "доставлен",
    "вручен",
    "получен",
    "выдан",
    "delivered",
    "received",
)

DELIVERED_FLAG_KEYS = ("isDelivered", "is_delivered", "delivered")


class OzonTrackingApiError(Exception):
    """Raised when tracking data can not be fetched or understood."""


class OzonTrackingForbiddenError(OzonTrackingApiError):
    """Raised when the anti-bot protection answers with HTTP 403."""


class OzonTrackingChallengeError(OzonTrackingForbiddenError):
    """Raised when Ozon returns its JavaScript anti-bot challenge.

    This can not be solved without either a real browser TLS fingerprint
    (curl_cffi) or a user supplied Cookie captured from a browser.
    """


def _is_challenge(body: str) -> bool:
    return any(marker in body for marker in CHALLENGE_MARKERS)


class OzonTrackingApi:
    """Minimal async client for tracking.ozon.ru.

    Ozon answers the anti-bot 403 challenge when a request does not look like
    its own web app. The decisive part (confirmed from a captured HAR) is the
    ``x-o3-app-name`` / ``x-o3-app-version`` application headers together with
    a current Chrome User-Agent — a working request needs no cookies at all.
    On top of that the client prefers a ``curl_cffi`` transport that also
    impersonates Chrome's TLS/HTTP2 fingerprint (falling back to aiohttp), and
    an optional user supplied ``Cookie`` header can still be replayed as a
    last-resort fallback.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        cookie: str | None = None,
        source: str = "track365",
        verify_ssl: bool = True,
    ) -> None:
        self._session = session
        self._cookie = (cookie or "").strip() or None
        self._source = (source or "track365").strip().lower()
        # None = default verification; False = skip (e.g. track365's cert has
        # expired). aiohttp accepts either as the request `ssl` argument.
        self._ssl = None if verify_ssl else False
        self._warmed_up = False
        self._curl: Any | None = None

    @property
    def uses_impersonation(self) -> bool:
        return HAS_CURL_CFFI

    @property
    def source(self) -> str:
        return self._source

    async def async_close(self) -> None:
        """Close the curl_cffi session, if one was created."""
        if self._curl is not None:
            try:
                await self._curl.close()
            except Exception:  # noqa: BLE001
                pass
            self._curl = None

    def _headers(self, referer: str | None, *, page: bool) -> dict[str, str]:
        headers = dict(COMMON_HEADERS)
        if not HAS_CURL_CFFI:
            # aiohttp does not fake a browser fingerprint, so add the header
            # half of it manually. With curl_cffi impersonation these are set
            # by the library and must not be overridden.
            headers.update(FINGERPRINT_HEADERS)
        if page:
            headers.update(
                {
                    "Accept": (
                        "text/html,application/xhtml+xml,application/xml;q=0.9,"
                        "image/avif,image/webp,*/*;q=0.8"
                    ),
                    "Sec-Fetch-Site": "none",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-User": "?1",
                    "Upgrade-Insecure-Requests": "1",
                }
            )
        else:
            headers.update(
                {
                    "Accept": "application/json, text/plain, */*",
                    "priority": "u=1, i",
                    "Sec-Fetch-Site": "same-origin",
                    "Sec-Fetch-Mode": "cors",
                    "Sec-Fetch-Dest": "empty",
                    **APP_HEADERS,
                }
            )
        if referer:
            headers["Referer"] = referer
        if self._cookie:
            headers["Cookie"] = self._cookie
        return headers

    async def _http_get(
        self, url: str, headers: dict[str, str], params: dict[str, str] | None = None
    ) -> tuple[int, str]:
        """Perform a GET returning (status_code, body) via the best transport."""
        if HAS_CURL_CFFI:
            if self._curl is None:
                self._curl = _CurlAsyncSession(
                    impersonate=CURL_IMPERSONATE, timeout=TIMEOUT_SECONDS
                )
            resp = await self._curl.get(url, headers=headers, params=params)
            return resp.status_code, resp.text
        async with self._session.get(
            url, headers=headers, params=params, timeout=REQUEST_TIMEOUT
        ) as resp:
            return resp.status, await resp.text()

    async def async_get_tracking(self, tracking_number: str) -> dict[str, Any]:
        """Fetch and normalize tracking info for a single tracking number."""
        track = tracking_number.strip()

        # Aggregator source: query track365.ru directly (no anti-bot, richer
        # data). This is the default and needs no cookie.
        if self._source == "track365":
            return await self._get_track365(track)

        referer = f"{PAGE_URL}?track={track}"
        errors: list[str] = []

        # Primary: the BFF JSON endpoint. On a 403 visit the tracking page
        # once to collect anti-bot cookies into the session jar and retry.
        payloads: list[Any] | None = None
        try:
            payloads = await self._fetch_bff(track, referer)
        except OzonTrackingForbiddenError as err:
            if not self._warmed_up:
                _LOGGER.debug("Got 403 for %s, warming up via tracking page", track)
                await self._async_warm_up(track)
                try:
                    payloads = await self._fetch_bff(track, referer)
                except (
                    OzonTrackingApiError,
                    aiohttp.ClientError,
                    asyncio.TimeoutError,
                ) as retry_err:
                    errors.append(f"bff-api (after warm-up): {retry_err}")
            else:
                errors.append(f"bff-api: {err}")
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            errors.append(f"bff-api: {err.__class__.__name__}: {err}")
        except OzonTrackingApiError as err:
            errors.append(f"bff-api: {err}")

        if payloads is not None:
            normalized = self._parse_payloads(payloads, track)
            if normalized is not None:
                return normalized
            errors.append("bff-api: unrecognized payload structure")

        # Fallback: scrape JSON embedded into the HTML page.
        try:
            payloads = await self._fetch_page(track)
            normalized = self._parse_payloads(payloads, track)
            if normalized is not None:
                return normalized
            errors.append("page: unrecognized payload structure")
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            errors.append(f"page: {err.__class__.__name__}: {err}")
        except OzonTrackingApiError as err:
            errors.append(f"page: {err}")

        if any("challenge" in item.lower() for item in errors) and not self._cookie:
            hint = (
                " Ozon anti-bot challenge is blocking requests. "
                "Set the 'Cookie' option (copy the cookie header from a "
                "browser that opened tracking.ozon.ru)"
            )
            if not HAS_CURL_CFFI:
                hint += "; the curl_cffi package is not installed"
            errors.append(hint.strip())

        raise OzonTrackingApiError(
            f"Could not fetch tracking data for {track}: " + "; ".join(errors)
        )

    async def _get_track365(self, track: str) -> dict[str, Any]:
        """Fetch tracking data from track365.ru (plain GET, no anti-bot)."""
        fp = _build_track365_fp(track)
        headers = {
            "Accept": "*/*",
            "Accept-Language": "ru,en;q=0.9",
            "Content-Type": "application/json",
            "cache": "no-store",
            "Referer": TRACK365_PAGE.format(tracking_number=quote(track, safe="")),
            "User-Agent": TRACK365_UA,
            "sec-ch-ua": '"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        }
        params = {"fp": fp, "r": str(random.randint(1, 10000))}

        async def _do_request(ssl_value: Any) -> tuple[int, str]:
            async with self._session.get(
                TRACK365_ENDPOINT,
                params=params,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
                ssl=ssl_value,
            ) as resp:
                return resp.status, await resp.text()

        try:
            status, body = await _do_request(self._ssl)
        except aiohttp.ClientSSLError as err:
            # track365 periodically lets its TLS certificate expire. If we were
            # verifying, transparently retry once without verification and stop
            # verifying for the rest of this session (avoids per-poll retries).
            if self._ssl is False:
                raise OzonTrackingApiError(
                    f"Could not reach track365.ru (TLS): {err}"
                ) from err
            _LOGGER.warning(
                "track365 TLS certificate rejected (%s); retrying without "
                "verification and disabling it for this session",
                err,
            )
            self._ssl = False
            try:
                status, body = await _do_request(False)
            except (aiohttp.ClientError, asyncio.TimeoutError) as err2:
                raise OzonTrackingApiError(
                    f"Could not reach track365.ru: {err2.__class__.__name__}: {err2}"
                ) from err2
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise OzonTrackingApiError(
                f"Could not reach track365.ru: {err.__class__.__name__}: {err}"
            ) from err

        if status != 200:
            raise OzonTrackingApiError(f"track365 HTTP {status}: {body[:200]!r}")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as err:
            # Not JSON — track365 served something else (HTML interstitial,
            # error page, etc.). Surface a snippet so the cause is visible.
            raise OzonTrackingApiError(
                f"track365 did not return JSON ({err}); body starts with "
                f"{body[:200]!r}"
            ) from err
        if isinstance(payload, dict) and payload.get("status") is False:
            raise OzonTrackingApiError(
                f"track365 reported no result for {track} "
                f"(unknown/expired tracking number?): {body[:200]!r}"
            )
        normalized = parse_track365(payload, track)
        if normalized is None:
            raise OzonTrackingApiError(
                f"track365 returned unrecognized data for {track}: {body[:200]!r}"
            )
        return normalized

    @staticmethod
    def _parse_payloads(payloads: list[Any], track: str) -> dict[str, Any] | None:
        for payload in payloads:
            normalized = parse_bff_payload(payload, track) or normalize_payload(
                payload, track
            )
            if normalized is not None:
                return normalized
        return None

    async def _async_warm_up(self, track: str) -> None:
        """Visit the tracking page once to pick up anti-bot cookies."""
        self._warmed_up = True
        try:
            status, _ = await self._http_get(
                PAGE_URL, self._headers(None, page=True), {"track": track}
            )
            _LOGGER.debug("Warm-up request returned HTTP %s", status)
            await asyncio.sleep(1.0)
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.debug("Warm-up request failed: %s", err)

    async def _fetch_bff(self, track: str, referer: str) -> list[Any]:
        url = API_ENDPOINT.format(tracking_number=quote(track, safe=""))
        status, body = await self._http_get(url, self._headers(referer, page=False))
        if status == 403:
            if _is_challenge(body):
                raise OzonTrackingChallengeError(
                    f"HTTP 403 anti-bot challenge: {body[:160]!r}"
                )
            raise OzonTrackingForbiddenError(f"HTTP 403: {body[:160]!r}")
        if status == 404:
            raise OzonTrackingApiError("HTTP 404: tracking number not found")
        if status != 200:
            raise OzonTrackingApiError(f"HTTP {status}")
        return [_loads(body)]

    async def _fetch_page(self, track: str) -> list[Any]:
        status, html = await self._http_get(
            PAGE_URL, self._headers(None, page=True), {"track": track}
        )
        if status != 200:
            raise OzonTrackingApiError(f"HTTP {status}: {html[:160]!r}")
        if _is_challenge(html):
            raise OzonTrackingChallengeError("HTML page returned anti-bot challenge")
        payloads = _extract_embedded_json(html)
        if not payloads:
            raise OzonTrackingApiError("no embedded JSON found in HTML page")
        return payloads


def _loads(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as err:
        raise OzonTrackingApiError(f"invalid JSON: {err}") from err


def _build_track365_fp(track: str) -> str:
    """Build track365's obfuscated ``fp`` query parameter.

    fp = base64( ",".join( str(ord(c) ^ 6) for c in PREFIX + UA + track ) ).
    Reverse-engineered from track365's l.js and verified byte-for-byte.
    """
    plain = TRACK365_FP_PREFIX + TRACK365_UA + track
    nums = ",".join(str(ord(c) ^ 6) for c in plain)
    return base64.b64encode(nums.encode()).decode()


def parse_track365(payload: Any, tracking_number: str) -> dict[str, Any] | None:
    """Normalize a track365.ru response to the shared tracking structure."""
    if not isinstance(payload, dict) or not payload.get("status"):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    raw_events = data.get("events")
    if not isinstance(raw_events, list):
        return None

    events: list[dict[str, Any]] = []
    courier: str | None = None
    for item in raw_events:
        if not isinstance(item, dict):
            continue
        text = (item.get("attribute") or item.get("place") or "").strip()
        moment = item.get("date")
        if courier is None and item.get("courier"):
            courier = str(item["courier"])
        place = str(item.get("place") or "")
        if text:
            events.append(
                {
                    "time": moment if isinstance(moment, str) else None,
                    "status": text,
                    "code": place or None,
                }
            )

    # "Delivered" must reflect the CURRENT state, not any historical event:
    # intermediate statuses like "Данные упаковки получены" would otherwise
    # trigger it. track365 lists events newest-first, so the parcel is
    # delivered only if the overall status says so or the latest event's place
    # code is a final-delivery marker (e.g. TYP_SUCCESS).
    # track365 returns events newest-first already; keep that order.
    overall = str(data.get("status") or "").lower()
    latest_place = ""
    for item in raw_events:
        if isinstance(item, dict):
            latest_place = str(item.get("place") or "")
            break
    delivered = (
        overall in TRACK365_DELIVERED_STATUSES
        or latest_place in TRACK365_DELIVERED_PLACES
    )

    status = events[0]["status"] if events else (data.get("status") or None)
    if status is None:
        return None

    return {
        "tracking_number": data.get("trackcode") or tracking_number,
        "status": status,
        "status_code": data.get("status"),
        "delivered": delivered,
        "events": events[:EVENTS_LIMIT],
        "courier": courier,
        "delivery_type": None,
        "estimated_delivery": None,
        "delivery_date_begin": None,
        "delivery_date_end": None,
        "source": "track365",
    }


def _humanize_code(code: str) -> str:
    """Turn an unknown event code like 'WayToCity' into 'Way to city'."""
    spaced = re.sub(r"(?<=[a-zа-я0-9])(?=[A-ZА-Я])", " ", code).replace("_", " ")
    return spaced[:1].upper() + spaced[1:].lower() if spaced else code


def _event_text(code: str) -> str:
    return EVENT_NAMES.get(code) or _humanize_code(code)


def _format_date(value: str | None) -> str | None:
    moment = _parse_time(value)
    return moment.strftime("%d.%m.%Y") if moment else None


def parse_bff_payload(payload: Any, tracking_number: str) -> dict[str, Any] | None:
    """Parse the known ozon-track-bff response shape.

    Returns ``None`` when the payload does not match, so the generic parser
    can have a try.
    """
    if not isinstance(payload, dict):
        return None
    items = payload.get("items")
    if not isinstance(items, list):
        return None

    events: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            return None
        code = item.get("event")
        if not isinstance(code, str) or not code:
            return None
        moment = item.get("moment")
        events.append(
            {
                "time": moment if isinstance(moment, str) else None,
                "status": _event_text(code),
                "code": code,
            }
        )

    # Items come oldest-first; expose newest-first like the rest of the code.
    parsed = [(_parse_time(event["time"]), event) for event in events]
    if parsed and all(moment is not None for moment, _ in parsed):
        parsed.sort(key=lambda pair: pair[0], reverse=True)  # type: ignore[arg-type, return-value]
        events = [event for _, event in parsed]
    else:
        events = list(reversed(events))

    if not events:
        return None
    latest = events[0]
    delivered = latest["code"] in DELIVERED_CODES or "delivered" in latest[
        "code"
    ].lower()

    date_begin = _format_date(payload.get("deliveryDateBegin"))
    date_end = _format_date(payload.get("deliveryDateEnd"))
    if date_begin and date_end and date_begin != date_end:
        estimated = f"{date_begin} – {date_end}"
    else:
        estimated = date_begin or date_end

    delivery_type = payload.get("deliveryType")
    if isinstance(delivery_type, str) and delivery_type not in ("", "Unknown"):
        delivery_type_name = DELIVERY_TYPE_NAMES.get(
            delivery_type, _humanize_code(delivery_type)
        )
    else:
        delivery_type_name = None

    return {
        "tracking_number": tracking_number,
        "status": latest["status"],
        "status_code": latest["code"],
        "delivered": delivered,
        "events": events[:EVENTS_LIMIT],
        "courier": None,
        "delivery_type": delivery_type_name,
        "estimated_delivery": estimated,
        "delivery_date_begin": payload.get("deliveryDateBegin"),
        "delivery_date_end": payload.get("deliveryDateEnd"),
        "source": "ozon",
    }


# --- generic fallback parser ----------------------------------------------


def _extract_embedded_json(html: str) -> list[Any]:
    """Pull JSON blobs out of an HTML page (Nuxt/Next/state scripts)."""
    payloads: list[Any] = []
    for match in re.finditer(
        r"<script[^>]*type=[\"']application/(?:ld\+)?json[\"'][^>]*>(.*?)</script>",
        html,
        re.S,
    ):
        try:
            payloads.append(json.loads(match.group(1).strip()))
        except json.JSONDecodeError:
            continue
    for match in re.finditer(
        r"window\.__(?:NUXT|INITIAL_STATE|NEXT_DATA|STATE)__\s*=\s*(\{.*?\})\s*(?:;\s*</script>|;\s*window\.|</script>)",
        html,
        re.S,
    ):
        try:
            payloads.append(json.loads(match.group(1)))
        except json.JSONDecodeError:
            continue
    return payloads


def _walk(obj: Any, depth: int = 0):
    if depth > 14:
        return
    yield obj
    if isinstance(obj, dict):
        for value in obj.values():
            yield from _walk(value, depth + 1)
    elif isinstance(obj, list):
        for value in obj:
            yield from _walk(value, depth + 1)


def _text_from(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in TEXT_KEYS:
            inner = value.get(key)
            if isinstance(inner, str) and inner.strip():
                return inner.strip()
        return None
    if isinstance(value, str):
        text = value.strip()
        if 0 < len(text) <= 200 and not text.startswith(("http://", "https://", "{", "[")):
            return text
    return None


def _find_status(payload: Any) -> str | None:
    best: str | None = None
    best_rank = len(STATUS_KEYS)
    for node in _walk(payload):
        if not isinstance(node, dict):
            continue
        for rank, key in enumerate(STATUS_KEYS):
            if rank >= best_rank or key not in node:
                continue
            text = _text_from(node[key])
            if text:
                best = text
                best_rank = rank
    return best


def _event_from_dict(item: dict[str, Any]) -> dict[str, Any] | None:
    text = None
    for key in TEXT_KEYS:
        candidate = _text_from(item.get(key))
        if candidate:
            text = candidate
            break
    if text is None:
        return None
    time_value = None
    for key in TIME_KEYS:
        raw = item.get(key)
        if isinstance(raw, (str, int, float)) and str(raw).strip():
            time_value = str(raw).strip()
            break
    return {"time": time_value, "status": text}


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if re.fullmatch(r"\d{13}", text):
        return datetime.fromtimestamp(int(text) / 1000, tz=timezone.utc)
    if re.fullmatch(r"\d{10}", text):
        return datetime.fromtimestamp(int(text), tz=timezone.utc)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _find_events(payload: Any) -> list[dict[str, Any]]:
    best: list[dict[str, Any]] = []
    best_score = 0
    for node in _walk(payload):
        if not (isinstance(node, list) and node and all(isinstance(i, dict) for i in node)):
            continue
        events = [event for event in (_event_from_dict(item) for item in node) if event]
        if len(events) < len(node):
            continue
        timed = sum(1 for event in events if event["time"])
        if timed == 0:
            continue
        score = timed * 2 + len(events)
        if score > best_score:
            best = events
            best_score = score
    parsed = [(_parse_time(event["time"]), event) for event in best]
    if parsed and all(moment is not None for moment, _ in parsed):
        parsed.sort(key=lambda pair: pair[0], reverse=True)  # type: ignore[arg-type, return-value]
        return [event for _, event in parsed]
    return best


def _find_first_text(payload: Any, keys: tuple[str, ...]) -> str | None:
    for node in _walk(payload):
        if not isinstance(node, dict):
            continue
        for key in keys:
            if key in node:
                text = _text_from(node[key])
                if text:
                    return text
    return None


def _find_delivered_flag(payload: Any) -> bool:
    for node in _walk(payload):
        if not isinstance(node, dict):
            continue
        for key in DELIVERED_FLAG_KEYS:
            if node.get(key) is True:
                return True
    return False


COURIER_KEYS = (
    "courierName",
    "courier_name",
    "courier",
    "carrierName",
    "carrier_name",
    "carrier",
    "deliveryService",
    "delivery_service",
    "transportCompany",
    "transport_company",
)

ETA_KEYS = (
    "plannedDeliveryDate",
    "planned_delivery_date",
    "estimatedDeliveryDate",
    "estimated_delivery_date",
    "expectedDeliveryDate",
    "expected_delivery_date",
    "deliveryDateEstimate",
    "deliveryDate",
    "delivery_date",
)


def normalize_payload(payload: Any, tracking_number: str) -> dict[str, Any] | None:
    """Schema-tolerant fallback: reduce an arbitrary payload to a flat dict.

    Returns ``None`` when the payload does not look like tracking data at all.
    """
    if payload is None:
        return None
    events = _find_events(payload)
    status = _find_status(payload)
    if status is None and events:
        status = events[0]["status"]
    if status is None and not events:
        return None

    haystack = " ".join(
        part.lower()
        for part in [status or "", events[0]["status"] if events else ""]
    )
    delivered = _find_delivered_flag(payload) or any(
        marker in haystack for marker in DELIVERED_MARKERS
    )

    return {
        "tracking_number": tracking_number,
        "status": status,
        "status_code": None,
        "delivered": delivered,
        "events": events[:EVENTS_LIMIT],
        "courier": _find_first_text(payload, COURIER_KEYS),
        "delivery_type": None,
        "estimated_delivery": _find_first_text(payload, ETA_KEYS),
        "delivery_date_begin": None,
        "delivery_date_end": None,
        "source": "ozon",
    }
