"""Data update coordinator for the Ozon Package Tracker integration."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from .api import OzonTrackingApi, OzonTrackingApiError
from .const import (
    CONF_AUTO_DELETE_DAYS,
    CONF_UPDATE_INTERVAL,
    DEFAULT_AUTO_DELETE_DAYS,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    EVENT_DATA_UPDATED,
    STORAGE_KEY,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

TRACKING_NUMBER_RE = re.compile(r"[0-9A-Za-zА-Яа-я][0-9A-Za-zА-Яа-я\-_/ ]{2,63}")

# Pause between per-package requests so we do not hammer the service.
REQUEST_SPACING = 1.5


class OzonPackageCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Polls tracking.ozon.ru for every stored package."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, api: OzonTrackingApi
    ) -> None:
        interval = entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=interval),
        )
        self._api = api
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._packages: dict[str, dict[str, Any]] = {}

    @staticmethod
    def unique_id_for(tracking_number: str) -> str:
        """Unique id used by the sensor entity of a package."""
        return f"{DOMAIN}_{slugify(tracking_number)}"

    def track_for_unique_id(self, unique_id: str) -> str | None:
        """Map a sensor unique id back to its tracking number."""
        for track in self._packages:
            if self.unique_id_for(track) == unique_id:
                return track
        return None

    async def async_load_store(self) -> None:
        stored = await self._store.async_load() or {}
        self._packages = stored.get("packages", {})

    async def _async_save(self) -> None:
        await self._store.async_save({"packages": self._packages})

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        now = dt_util.utcnow()
        options = self.config_entry.options if self.config_entry else {}
        auto_delete_days = options.get(CONF_AUTO_DELETE_DAYS, DEFAULT_AUTO_DELETE_DAYS)

        results: dict[str, dict[str, Any]] = {}
        store_dirty = False
        tracks = list(self._packages)

        for index, track in enumerate(tracks):
            meta = self._packages[track]
            if index:
                await asyncio.sleep(REQUEST_SPACING)
            try:
                info = await self._api.async_get_tracking(track)
                meta["last_data"] = info
                meta["last_success"] = now.isoformat()
                store_dirty = True
            except OzonTrackingApiError as err:
                _LOGGER.warning("Could not update Ozon package %s: %s", track, err)
                info = meta.get("last_data") or {
                    "tracking_number": track,
                    "status": None,
                    "status_code": None,
                    "delivered": False,
                    "events": [],
                    "courier": None,
                    "delivery_type": None,
                    "estimated_delivery": None,
                    "delivery_date_begin": None,
                    "delivery_date_end": None,
                }

            if info.get("delivered"):
                if not meta.get("delivered_at"):
                    meta["delivered_at"] = now.isoformat()
                    store_dirty = True
                if auto_delete_days:
                    delivered_at = dt_util.parse_datetime(meta["delivered_at"])
                    if delivered_at and now - delivered_at > timedelta(
                        days=auto_delete_days
                    ):
                        _LOGGER.info(
                            "Removing delivered Ozon package %s (auto-delete after %s days)",
                            track,
                            auto_delete_days,
                        )
                        del self._packages[track]
                        store_dirty = True
                        continue

            results[track] = {
                **info,
                "title": meta.get("title") or track,
                "added_at": meta.get("added_at"),
                "last_success": meta.get("last_success"),
            }

        previous = self.data or {}
        for track, new_info in results.items():
            old_status = (previous.get(track) or {}).get("status")
            new_status = new_info.get("status")
            if old_status is not None and new_status is not None and old_status != new_status:
                self.hass.bus.async_fire(
                    EVENT_DATA_UPDATED,
                    {
                        "tracking_number": track,
                        "title": new_info.get("title"),
                        "old_status": old_status,
                        "new_status": new_status,
                        "delivered": new_info.get("delivered", False),
                    },
                )

        if store_dirty:
            await self._async_save()
        return results

    async def async_add_package(self, tracking_number: str, title: str | None) -> None:
        """Add a package (or update the title of an existing one) and refresh."""
        track = tracking_number.strip()
        if not TRACKING_NUMBER_RE.fullmatch(track):
            raise HomeAssistantError(f"Invalid tracking number: {tracking_number!r}")

        existing = self._packages.get(track)
        if existing is not None:
            if title:
                existing["title"] = title.strip()
                await self._async_save()
                await self.async_request_refresh()
            return

        self._packages[track] = {
            "title": (title or "").strip() or track,
            "added_at": dt_util.utcnow().isoformat(),
            "delivered_at": None,
            "last_data": None,
            "last_success": None,
        }
        await self._async_save()
        await self.async_request_refresh()

    async def async_remove_package(self, tracking_number: str) -> None:
        """Remove a package from the store and drop its data immediately."""
        track = tracking_number.strip()
        if track not in self._packages:
            raise HomeAssistantError(f"Unknown tracking number: {tracking_number!r}")
        del self._packages[track]
        await self._async_save()
        remaining = {k: v for k, v in (self.data or {}).items() if k != track}
        self.async_set_updated_data(remaining)

    async def async_set_title(self, tracking_number: str, title: str) -> None:
        """Rename a package."""
        track = tracking_number.strip()
        meta = self._packages.get(track)
        if meta is None:
            raise HomeAssistantError(f"Unknown tracking number: {tracking_number!r}")
        meta["title"] = title.strip() or track
        await self._async_save()
        data = dict(self.data or {})
        if track in data:
            data[track] = {**data[track], "title": meta["title"]}
        self.async_set_updated_data(data)
