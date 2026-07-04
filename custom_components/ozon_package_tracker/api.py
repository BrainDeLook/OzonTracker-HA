"""Client for the public tracking.ozon.ru parcel tracking service.

There is no official public documentation for this endpoint, so the client is
deliberately defensive:

1. It first tries the JSON API used by the tracking web page
   (``GET https://tracking.ozon.ru/api/tracking?trackingNumber=...``).
2. Then a ``POST`` variant of the same endpoint.
3. As a last resort it downloads the HTML page and extracts any embedded
   JSON state (Nuxt/Next style payloads).

The response parser does not rely on an exact schema either: it recursively
searches the payload for a status string and a list of tracking events, so
minor changes on Ozon's side keep working without a code update.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

API_ENDPOINT = "https://tracking.ozon.ru/api/tracking"
PAGE_URL = "https://tracking.ozon.ru/"

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}

# Keys that may contain human readable event/status text.
TEXT_KEYS = (
    "statusText",
    "status_text",
    "statusName",
    "status_name",
    "name",
    "title",
    "status",
    "text",
    "description",
    "message",
)

# Keys that may contain an event timestamp.
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

# Keys that may contain the current overall status, in priority order.
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


class OzonTrackingApi:
    """Minimal async client for tracking.ozon.ru."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def async_get_tracking(self, tracking_number: str) -> dict[str, Any]:
        """Fetch and normalize tracking info for a single tracking number."""
        track = tracking_number.strip()
        referer = f"{PAGE_URL}?track={track}"
        errors: list[str] = []

        attempts = (
            ("api-get", self._fetch_api_get),
            ("api-post", self._fetch_api_post),
            ("page", self._fetch_page),
        )
        for name, attempt in attempts:
            try:
                payloads = await attempt(track, referer)
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                errors.append(f"{name}: {err.__class__.__name__}: {err}")
                continue
            except OzonTrackingApiError as err:
                errors.append(f"{name}: {err}")
                continue
            for payload in payloads:
                normalized = normalize_payload(payload, track)
                if normalized is not None:
                    return normalized
            errors.append(f"{name}: unrecognized payload structure")

        raise OzonTrackingApiError(
            f"Could not fetch tracking data for {track}: " + "; ".join(errors)
        )

    async def _fetch_api_get(self, track: str, referer: str) -> list[Any]:
        headers = {
            **BROWSER_HEADERS,
            "Accept": "application/json, text/plain, */*",
            "Referer": referer,
        }
        async with self._session.get(
            API_ENDPOINT,
            params={"trackingNumber": track},
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        ) as resp:
            if resp.status != 200:
                raise OzonTrackingApiError(f"HTTP {resp.status}")
            text = await resp.text()
        return [_loads(text)]

    async def _fetch_api_post(self, track: str, referer: str) -> list[Any]:
        headers = {
            **BROWSER_HEADERS,
            "Accept": "application/json, text/plain, */*",
            "Referer": referer,
        }
        async with self._session.post(
            API_ENDPOINT,
            json={"trackingNumber": track},
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        ) as resp:
            if resp.status != 200:
                raise OzonTrackingApiError(f"HTTP {resp.status}")
            text = await resp.text()
        return [_loads(text)]

    async def _fetch_page(self, track: str, referer: str) -> list[Any]:
        headers = {
            **BROWSER_HEADERS,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        async with self._session.get(
            PAGE_URL,
            params={"track": track},
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        ) as resp:
            if resp.status != 200:
                raise OzonTrackingApiError(f"HTTP {resp.status}")
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
    text = value.strip()
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


def normalize_payload(payload: Any, tracking_number: str) -> dict[str, Any] | None:
    """Reduce an arbitrary tracking payload to a stable, flat structure.

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
        "delivered": delivered,
        "events": events[:20],
        "courier": _find_first_text(payload, COURIER_KEYS),
        "estimated_delivery": _find_first_text(payload, ETA_KEYS),
    }
