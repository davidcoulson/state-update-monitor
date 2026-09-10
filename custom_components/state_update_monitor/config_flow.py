"""Config flow for State Update Monitor.

Only one instance is allowed since it monitors the whole event bus;
per-instance filtering is handled via the exclude/include fields
rather than by adding multiple entries.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_EXCLUDE_DOMAINS,
    CONF_INCLUDE_FILTER,
    CONF_TOP_N,
    CONF_UPDATE_INTERVAL,
    CONF_WINDOW_SECONDS,
    DEFAULT_TOP_N,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_WINDOW_SECONDS,
    DOMAIN,
)


def _build_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_WINDOW_SECONDS,
                default=defaults.get(CONF_WINDOW_SECONDS, DEFAULT_WINDOW_SECONDS),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=10, max=3600, step=10, unit_of_measurement="s", mode="box"
                )
            ),
            vol.Required(
                CONF_TOP_N, default=defaults.get(CONF_TOP_N, DEFAULT_TOP_N)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=100, step=1, mode="box")
            ),
            vol.Required(
                CONF_UPDATE_INTERVAL,
                default=defaults.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=5, max=600, step=5, unit_of_measurement="s", mode="box"
                )
            ),
            vol.Optional(
                CONF_EXCLUDE_DOMAINS, default=defaults.get(CONF_EXCLUDE_DOMAINS, "")
            ): selector.TextSelector(),
            vol.Optional(
                CONF_INCLUDE_FILTER, default=defaults.get(CONF_INCLUDE_FILTER, "")
            ): selector.TextSelector(),
        }
    )


class StateUpdateMonitorConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the initial setup flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title="State Update Monitor", data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=_build_schema({}), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> StateUpdateMonitorOptionsFlow:
        return StateUpdateMonitorOptionsFlow()


class StateUpdateMonitorOptionsFlow(config_entries.OptionsFlow):
    """Let the user change settings later from Settings -> Devices & Services.

    No __init__ override, and no manual `self.config_entry = ...` assignment:
    since HA 2024.12, the base OptionsFlow class exposes `self.config_entry`
    as a property that the flow manager binds automatically. Setting it
    yourself is what triggers the deprecation warning on newer cores.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(step_id="init", data_schema=_build_schema(current))
