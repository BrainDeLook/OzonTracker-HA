"""Constants for the Ozon Package Tracker integration."""

from __future__ import annotations

DOMAIN = "ozon_package_tracker"
PLATFORMS: list[str] = ["sensor"]

STORAGE_KEY = f"{DOMAIN}.packages"
STORAGE_VERSION = 1

CONF_UPDATE_INTERVAL = "update_interval"
CONF_AUTO_DELETE_DAYS = "auto_delete_days"
CONF_COOKIE = "cookie"
CONF_PROXY_URL = "proxy_url"
CONF_SOURCE = "source"
CONF_VERIFY_SSL = "verify_ssl"

SOURCE_TRACK365 = "track365"
SOURCE_OZON = "ozon"
DEFAULT_SOURCE = SOURCE_TRACK365
DEFAULT_VERIFY_SSL = True

DEFAULT_UPDATE_INTERVAL = 30  # minutes
DEFAULT_AUTO_DELETE_DAYS = 0  # 0 = keep delivered packages forever

ATTR_TRACKING_NUMBER = "tracking_number"
ATTR_TITLE = "title"
ATTR_ENTITY_ID = "entity_id"

SERVICE_ADD_TRACKING = "add_tracking"
SERVICE_REMOVE_TRACKING = "remove_tracking"
SERVICE_EDIT_TITLE = "edit_title"
SERVICE_REFRESH = "refresh"

EVENT_DATA_UPDATED = f"{DOMAIN}_data_updated"

TRACKING_PAGE_URL = "https://tracking.ozon.ru/?track={tracking_number}"

CARD_FILENAME = "ozon-package-card.js"
FRONTEND_URL_BASE = f"/{DOMAIN}"
