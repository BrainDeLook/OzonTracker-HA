"""The Ozon Package Tracker integration."""

from __future__ import annotations

import logging
from pathlib import Path

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .api import OzonTrackingApi
from .const import (
    ATTR_ENTITY_ID,
    ATTR_TITLE,
    ATTR_TRACKING_NUMBER,
    CARD_FILENAME,
    CONF_COOKIE,
    CONF_PROXY_URL,
    CONF_SOURCE,
    DEFAULT_SOURCE,
    DOMAIN,
    FRONTEND_URL_BASE,
    PLATFORMS,
    SERVICE_ADD_TRACKING,
    SERVICE_EDIT_TITLE,
    SERVICE_REFRESH,
    SERVICE_REMOVE_TRACKING,
)
from .coordinator import OzonPackageCoordinator

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

ADD_TRACKING_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_TRACKING_NUMBER): cv.string,
        vol.Optional(ATTR_TITLE): cv.string,
    }
)

REMOVE_TRACKING_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Optional(ATTR_TRACKING_NUMBER): cv.string,
            vol.Optional(ATTR_ENTITY_ID): cv.entity_ids,
        }
    ),
    cv.has_at_least_one_key(ATTR_TRACKING_NUMBER, ATTR_ENTITY_ID),
)

EDIT_TITLE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_TRACKING_NUMBER): cv.string,
        vol.Required(ATTR_TITLE): cv.string,
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register services and the bundled Lovelace card."""
    _async_register_services(hass)
    await _async_setup_frontend(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Ozon Package Tracker from a config entry."""
    # Dedicated session with its own cookie jar: Ozon's anti-bot protection
    # hands out cookies that must be replayed on subsequent requests.
    session = async_create_clientsession(hass)
    api = OzonTrackingApi(
        session,
        cookie=entry.options.get(CONF_COOKIE),
        proxy_url=entry.options.get(CONF_PROXY_URL),
        source=entry.options.get(CONF_SOURCE, DEFAULT_SOURCE),
    )
    coordinator = OzonPackageCoordinator(hass, entry, api)
    await coordinator.async_load_store()
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator = hass.data[DOMAIN].pop(entry.entry_id, None)
        if coordinator is not None:
            await coordinator.async_close()
    return unload_ok


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply new options by reloading the entry."""
    await hass.config_entries.async_reload(entry.entry_id)


def _get_coordinator(hass: HomeAssistant) -> OzonPackageCoordinator:
    for value in (hass.data.get(DOMAIN) or {}).values():
        if isinstance(value, OzonPackageCoordinator):
            return value
    raise HomeAssistantError(
        "Ozon Package Tracker is not set up. Add the integration first."
    )


def _tracks_from_call(hass: HomeAssistant, call: ServiceCall) -> list[str]:
    """Resolve tracking numbers from service call data."""
    from homeassistant.helpers import entity_registry as er

    coordinator = _get_coordinator(hass)
    tracks: list[str] = []
    if track := call.data.get(ATTR_TRACKING_NUMBER):
        tracks.append(track)
    if entity_ids := call.data.get(ATTR_ENTITY_ID):
        registry = er.async_get(hass)
        for entity_id in entity_ids:
            entry = registry.async_get(entity_id)
            resolved = None
            if entry and entry.platform == DOMAIN:
                resolved = coordinator.track_for_unique_id(entry.unique_id)
            if resolved is None:
                state = hass.states.get(entity_id)
                if state:
                    resolved = state.attributes.get(ATTR_TRACKING_NUMBER)
            if resolved is None:
                raise HomeAssistantError(
                    f"{entity_id} is not an Ozon Package Tracker sensor"
                )
            tracks.append(resolved)
    return tracks


def _async_register_services(hass: HomeAssistant) -> None:
    async def handle_add(call: ServiceCall) -> None:
        coordinator = _get_coordinator(hass)
        await coordinator.async_add_package(
            call.data[ATTR_TRACKING_NUMBER], call.data.get(ATTR_TITLE)
        )

    async def handle_remove(call: ServiceCall) -> None:
        coordinator = _get_coordinator(hass)
        for track in _tracks_from_call(hass, call):
            await coordinator.async_remove_package(track)

    async def handle_edit_title(call: ServiceCall) -> None:
        coordinator = _get_coordinator(hass)
        await coordinator.async_set_title(
            call.data[ATTR_TRACKING_NUMBER], call.data[ATTR_TITLE]
        )

    async def handle_refresh(call: ServiceCall) -> None:
        await _get_coordinator(hass).async_request_refresh()

    hass.services.async_register(
        DOMAIN, SERVICE_ADD_TRACKING, handle_add, schema=ADD_TRACKING_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_REMOVE_TRACKING, handle_remove, schema=REMOVE_TRACKING_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_EDIT_TITLE, handle_edit_title, schema=EDIT_TITLE_SCHEMA
    )
    hass.services.async_register(DOMAIN, SERVICE_REFRESH, handle_refresh)


async def _async_setup_frontend(hass: HomeAssistant) -> None:
    """Serve the bundled Lovelace card and register it as a resource."""
    flag = f"{DOMAIN}_frontend_registered"
    if hass.data.get(flag):
        return
    hass.data[flag] = True

    card_dir = Path(__file__).parent / "lovelace"
    try:
        from homeassistant.components.http import StaticPathConfig

        await hass.http.async_register_static_paths(
            [StaticPathConfig(FRONTEND_URL_BASE, str(card_dir), False)]
        )
    except ImportError:
        hass.http.register_static_path(FRONTEND_URL_BASE, str(card_dir), False)

    integration = await async_get_integration(hass, DOMAIN)
    version = integration.version or "0"
    card_url = f"{FRONTEND_URL_BASE}/{CARD_FILENAME}?v={version}"

    async def register_resource(*_args) -> None:
        if await _async_add_lovelace_resource(hass, card_url):
            return
        # YAML-mode dashboards: inject the module directly instead.
        try:
            from homeassistant.components.frontend import add_extra_js_url

            add_extra_js_url(hass, card_url)
        except Exception:  # noqa: BLE001
            _LOGGER.warning(
                "Could not register %s automatically; add it manually as a "
                "Lovelace resource (type: module)",
                card_url,
            )

    if hass.state is CoreState.running:
        await register_resource()
    else:
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, register_resource)


async def _async_add_lovelace_resource(hass: HomeAssistant, card_url: str) -> bool:
    """Add or update the card in the Lovelace resource registry."""
    try:
        lovelace = hass.data.get("lovelace")
        resources = getattr(lovelace, "resources", None)
        if resources is None and isinstance(lovelace, dict):
            resources = lovelace.get("resources")
        if resources is None:
            return False

        if hasattr(resources, "async_load") and not getattr(resources, "loaded", True):
            await resources.async_load()
            resources.loaded = True

        base_url = card_url.split("?")[0]
        for item in resources.async_items():
            item_url = item.get("url", "")
            if item_url.split("?")[0] != base_url:
                continue
            if item_url != card_url and hasattr(resources, "async_update_item"):
                await resources.async_update_item(item["id"], {"url": card_url})
                _LOGGER.debug("Updated Lovelace resource to %s", card_url)
            return True

        if hasattr(resources, "async_create_item"):
            await resources.async_create_item({"res_type": "module", "url": card_url})
            _LOGGER.info("Registered Lovelace resource %s", card_url)
            return True
        return False
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Failed to register the Ozon package card resource")
        return False
