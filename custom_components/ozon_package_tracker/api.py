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
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import aiohttp

_LOGGER = logging.getLogger(__name__)

API_ENDPOINT = "https://tracking.ozon.ru/p-api/ozon-track-bff/tracking/{tracking_number}"
PAGE_URL = "https://tracking.ozon.ru/"

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "sec-ch-ua": '"Not/A)Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
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


class OzonTrackingApi:
    """Minimal async client for tracking.ozon.ru.

    The session should have its own cookie jar: Ozon's anti-bot protection
    hands out cookies that must be replayed on subsequent requests. An
    optional user supplied ``Cookie`` header (copied from a real browser)
    can be passed for installations where the automatic warm-up is not
    enough to satisfy the protection.
    """

    def __init__(
        self, session: aiohttp.ClientSession, cookie: str | None = None
    ) -> None:
        self._session = session
        self._cookie = (cookie or "").strip() or None
        self._warmed_up = False

    def _base_headers(self) -> dict[str, str]:
        headers = dict(BROWSER_HEADERS)
        if self._cookie:
            headers["Cookie"] = self._cookie
        return headers

    def _api_headers(self, referer: str) -> dict[str, str]:
        return {
            **self._base_headers(),
            "Accept": "application/json, text/plain, */*",
            "Referer": referer,
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
        }

    def _page_headers(self) -> dict[str, str]:
        return {
            **self._base_headers(),
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

    async def async_get_tracking(self, tracking_number: str) -> dict[str, Any]:
        """Fetch and normalize tracking info for a single tracking number."""
        track = tracking_number.strip()
        referer = f"{PAGE_URL}?track={track}"
        errors: list[str] = []

        # Primary: the BFF JSON endpoint. On a 403 visit the tracking page
        # once to collect anti-bot cookies into the session jar and retry.
        payloads: list[Any] | None = None
        try:
            payloads = await self._fetch_bff(track, referer)
        except OzonTrackingForbiddenError as err:
            if not self._warmed_up:
                _LOGGER.debug(
                    "Got 403 for %s, warming up cookies via tracking page", track
                )
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

        raise OzonTrackingApiError(
            f"Could not fetch tracking data for {track}: " + "; ".join(errors)
        )

    @staticmethod
    def _parse_payloads(
        payloads: list[Any], track: str
    ) -> dict[str, Any] | None:
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
            async with self._session.get(
                PAGE_URL,
                params={"track": track},
                headers=self._page_headers(),
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                await resp.read()
                _LOGGER.debug("Warm-up request returned HTTP %s", resp.status)
            await asyncio.sleep(1.0)
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.debug("Warm-up request failed: %s", err)

    async def _fetch_bff(self, track: str, referer: str) -> list[Any]:
        url = API_ENDPOINT.format(tracking_number=quote(track, safe=""))
        async with self._session.get(
            url, headers=self._api_headers(referer), timeout=REQUEST_TIMEOUT
        ) as resp:
            if resp.status == 403:
                snippet = (await resp.text())[:200]
                raise OzonTrackingForbiddenError(
                    f"HTTP 403 (anti-bot): {snippet!r}"
                )
            if resp.status == 404:
                raise OzonTrackingApiError("HTTP 404: tracking number not found")
            if resp.status != 200:
                raise OzonTrackingApiError(f"HTTP {resp.status}")
            text = await resp.text()
        return [_loads(text)]

    async def _fetch_page(self, track: str) -> list[Any]:
        async with self._session.get(
            PAGE_URL,
            params={"track": track},
            headers=self._page_headers(),
            timeout=REQUEST_TIMEOUT,
        ) as resp:
            if resp.status != 200:
                snippet = (await resp.text())[:200]
                raise OzonTrackingApiError(f"HTTP {resp.status}: {snippet!r}")
            html = await resp.text()
        payloads = _extract_embedded_json(html)
        if not payloads:
            raise OzonTrackingApiError("no embedded JSON found in HTML page")
        return payloads


def _loads(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as err:
        raise OzonTrackingApiError(f"invalid JSON: {err}") from err


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
        "events": events[:20],
        "courier": None,
        "delivery_type": delivery_type_name,
        "estimated_delivery": estimated,
        "delivery_date_begin": payload.get("deliveryDateBegin"),
        "delivery_date_end": payload.get("deliveryDateEnd"),
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
        "events": events[:20],
        "courier": _find_first_text(payload, COURIER_KEYS),
        "delivery_type": None,
        "estimated_delivery": _find_first_text(payload, ETA_KEYS),
        "delivery_date_begin": None,
        "delivery_date_end": None,
    }
