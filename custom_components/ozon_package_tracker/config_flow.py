"""Config flow for the Ozon Package Tracker integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_AUTO_DELETE_DAYS,
    CONF_COOKIE,
    CONF_LINK_TARGET,
    CONF_SOURCE,
    CONF_UPDATE_INTERVAL,
    CONF_VERIFY_SSL,
    DEFAULT_AUTO_DELETE_DAYS,
    DEFAULT_LINK_TARGET,
    DEFAULT_SOURCE,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    LINK_TARGET_AUTO,
    LINK_TARGET_OZON,
    LINK_TARGET_TRACK365,
    MAX_UPDATE_INTERVAL,
    MIN_UPDATE_INTERVAL,
    SOURCE_OZON,
    SOURCE_TRACK365,
)


class OzonPackageTrackerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the (single instance) config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return self.async_create_entry(title="Ozon Package Tracker", data={})
        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return OzonPackageTrackerOptionsFlow()


class OzonPackageTrackerOptionsFlow(OptionsFlow):
    """Update interval and auto-delete options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SOURCE,
                    default=options.get(CONF_SOURCE, DEFAULT_SOURCE),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[SOURCE_TRACK365, SOURCE_OZON],
                        translation_key="source",
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(
                    CONF_VERIFY_SSL,
                    default=options.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
                ): selector.BooleanSelector(),
                vol.Optional(
                    CONF_LINK_TARGET,
                    default=options.get(CONF_LINK_TARGET, DEFAULT_LINK_TARGET),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[LINK_TARGET_AUTO, LINK_TARGET_TRACK365, LINK_TARGET_OZON],
                        translation_key="link_target",
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(
                    CONF_UPDATE_INTERVAL,
                    default=options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_UPDATE_INTERVAL, max=MAX_UPDATE_INTERVAL),
                ),
                vol.Optional(
                    CONF_AUTO_DELETE_DAYS,
                    default=options.get(
                        CONF_AUTO_DELETE_DAYS, DEFAULT_AUTO_DELETE_DAYS
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=90)),
                vol.Optional(
                    CONF_COOKIE,
                    description={"suggested_value": options.get(CONF_COOKIE, "")},
                ): selector.TextSelector(
                    selector.TextSelectorConfig(multiline=True)
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
