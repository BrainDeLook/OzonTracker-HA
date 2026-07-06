"""Sensor platform for the Ozon Package Tracker integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import ATTR_TRACKING_NUMBER, DOMAIN, TRACKING_PAGE_URL
from .coordinator import OzonPackageCoordinator

PARALLEL_UPDATES = 0

MAX_EVENT_ATTRIBUTES = 60


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one sensor per tracked package, following coordinator data."""
    coordinator: OzonPackageCoordinator = hass.data[DOMAIN][entry.entry_id]
    known: dict[str, OzonPackageSensor] = {}

    @callback
    def sync_entities() -> None:
        current = set(coordinator.data or {})

        new_entities = [
            OzonPackageSensor(coordinator, track)
            for track in current
            if track not in known
        ]
        for entity in new_entities:
            known[entity.tracking_number] = entity
        if new_entities:
            async_add_entities(new_entities)

        removed = [track for track in known if track not in current]
        if removed:
            registry = er.async_get(hass)
            for track in removed:
                entity = known.pop(track)
                entity_id = registry.async_get_entity_id(
                    "sensor", DOMAIN, entity.unique_id
                )
                if entity_id:
                    registry.async_remove(entity_id)

    entry.async_on_unload(coordinator.async_add_listener(sync_entities))
    sync_entities()


class OzonPackageSensor(
    CoordinatorEntity[OzonPackageCoordinator], SensorEntity
):
    """State of a single Ozon package."""

    _attr_should_poll = False
    _attr_attribution = "track365.ru / tracking.ozon.ru"

    def __init__(self, coordinator: OzonPackageCoordinator, track: str) -> None:
        super().__init__(coordinator)
        self.tracking_number = track
        self._attr_unique_id = coordinator.unique_id_for(track)
        self.entity_id = f"sensor.ozon_package_{slugify(track)}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "tracker")},
            name="Ozon Package Tracker",
            manufacturer="Ozon",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://tracking.ozon.ru/",
        )

    @property
    def _info(self) -> dict[str, Any] | None:
        return (self.coordinator.data or {}).get(self.tracking_number)

    @property
    def available(self) -> bool:
        return self._info is not None

    @property
    def name(self) -> str:
        info = self._info or {}
        title = info.get("title")
        if title and title != self.tracking_number:
            return title
        return f"Ozon посылка {self.tracking_number}"

    @property
    def native_value(self) -> str:
        info = self._info or {}
        return info.get("status") or "Нет данных"

    @property
    def icon(self) -> str:
        info = self._info or {}
        status = (info.get("status") or "").lower()
        if info.get("delivered"):
            return "mdi:package-variant-closed-check"
        if any(
            word in status
            for word in ("отмен", "возврат", "возвращ", "отказ", "cancel", "return")
        ):
            return "mdi:package-variant-remove"
        if any(word in status for word in ("выдач", "пункт", "постамат", "pickup")):
            return "mdi:package-down"
        if not status:
            return "mdi:package-variant-closed"
        return "mdi:truck-delivery-outline"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        info = self._info or {}
        events = (info.get("events") or [])[:MAX_EVENT_ATTRIBUTES]
        last_event = events[0] if events else None
        return {
            "integration": DOMAIN,
            "title": info.get("title") or self.tracking_number,
            ATTR_TRACKING_NUMBER: self.tracking_number,
            "delivered": info.get("delivered", False),
            "status_code": info.get("status_code"),
            "courier": info.get("courier"),
            "delivery_type": info.get("delivery_type"),
            "estimated_delivery": info.get("estimated_delivery"),
            "delivery_date_begin": info.get("delivery_date_begin"),
            "delivery_date_end": info.get("delivery_date_end"),
            "last_event": last_event["status"] if last_event else None,
            "last_event_time": last_event["time"] if last_event else None,
            "events": events,
            "added_at": info.get("added_at"),
            "last_update_success": info.get("last_success"),
            "tracking_url": TRACKING_PAGE_URL.format(
                tracking_number=self.tracking_number
            ),
        }
