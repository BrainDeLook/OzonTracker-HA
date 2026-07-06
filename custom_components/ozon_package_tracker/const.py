"""Constants for the Ozon Package Tracker integration."""

from __future__ import annotations

DOMAIN = "ozon_package_tracker"
PLATFORMS: list[str] = ["sensor"]

STORAGE_KEY = f"{DOMAIN}.packages"
STORAGE_VERSION = 1

CONF_UPDATE_INTERVAL = "update_interval"
CONF_AUTO_DELETE_DAYS = "auto_delete_days"
CONF_COOKIE = "cookie"
CONF_SOURCE = "source"
CONF_VERIFY_SSL = "verify_ssl"
CONF_LINK_TARGET = "link_target"
CONF_NOTIFY_TARGETS = "notify_targets"
CONF_NOTIFY_LEVEL = "notify_level"

SOURCE_TRACK365 = "track365"
SOURCE_OZON = "ozon"
DEFAULT_SOURCE = SOURCE_TRACK365
DEFAULT_VERIFY_SSL = True

# Where the card's "open tracking page" link points. "auto" follows whichever
# source actually produced the data, independent of CONF_SOURCE — e.g. data
# can be fetched via track365 while the link opens tracking.ozon.ru, or
# vice versa.
LINK_TARGET_AUTO = "auto"
LINK_TARGET_TRACK365 = SOURCE_TRACK365
LINK_TARGET_OZON = SOURCE_OZON
DEFAULT_LINK_TARGET = LINK_TARGET_AUTO

DEFAULT_UPDATE_INTERVAL = 60  # minutes
MIN_UPDATE_INTERVAL = 30  # minutes (protect the upstream service)
MAX_UPDATE_INTERVAL = 1440  # minutes
DEFAULT_AUTO_DELETE_DAYS = 0  # 0 = keep delivered packages forever

# Notification level: "all" pushes on every status change, "pickup" only when
# the status text indicates the parcel reached a pickup point/locker.
NOTIFY_LEVEL_ALL = "all"
NOTIFY_LEVEL_PICKUP = "pickup"
DEFAULT_NOTIFY_LEVEL = NOTIFY_LEVEL_ALL

# Status keywords meaning "ready at a pickup point/locker" — shared between
# the pickup-only notification filter and the card icon selection.
PICKUP_STATUS_KEYWORDS = ("выдач", "пункт", "постамат", "pickup")

ATTR_TRACKING_NUMBER = "tracking_number"
ATTR_TITLE = "title"
ATTR_ENTITY_ID = "entity_id"

SERVICE_ADD_TRACKING = "add_tracking"
SERVICE_REMOVE_TRACKING = "remove_tracking"
SERVICE_EDIT_TITLE = "edit_title"
SERVICE_REFRESH = "refresh"

EVENT_DATA_UPDATED = f"{DOMAIN}_data_updated"

TRACKING_PAGE_URL = "https://tracking.ozon.ru/?track={tracking_number}"
TRACK365_PAGE_URL = "https://track365.ru/?track={tracking_number}"

CARD_FILENAME = "ozon-package-card.js"
FRONTEND_URL_BASE = f"/{DOMAIN}"
