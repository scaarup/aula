import logging
from typing import Any, Dict, Optional

from homeassistant import config_entries
from homeassistant.const import CONF_USERNAME, CONF_PASSWORD
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity_registry import (
    async_entries_for_config_entry,
    async_get,
)
import voluptuous as vol

from .const import CONF_SCHOOLSCHEDULE, CONF_UGEPLAN, CONF_MU_OPGAVER, DOMAIN

_LOGGER = logging.getLogger(__name__)

AUTH_SCHEMA = vol.Schema(
    {
            vol.Required(CONF_USERNAME): cv.string, vol.Required(CONF_PASSWORD): cv.string, vol.Optional("schoolschedule"): cv.boolean,vol.Optional("ugeplan"): cv.boolean,vol.Optional("mu_opgaver"): cv.boolean
    }
)

class AulaCustomConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Aula Custom config flow."""
    data: Optional[Dict[str, Any]]

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None):
        """Invoked when a user initiates a flow via the user interface."""
        errors: Dict[str, str] = {}
        if user_input is not None:
            self.data = user_input
            _LOGGER.debug(user_input.get("schoolschedule"))
            if user_input.get("schoolschedule") == None:
                self.data[CONF_SCHOOLSCHEDULE] = False
            else:
                self.data[CONF_SCHOOLSCHEDULE] = user_input.get("schoolschedule")
            _LOGGER.debug(user_input.get("ugeplan"))
            if user_input.get("ugeplan") == None:
                self.data[CONF_UGEPLAN] = False
            else:
                self.data[CONF_UGEPLAN] = user_input.get("ugeplan")
            if user_input.get("mu_opgaver") == None:
                self.data[CONF_MU_OPGAVER] = False
            else:
                self.data[CONF_MU_OPGAVER] = user_input.get("mu_opgaver")
            _LOGGER.debug(user_input.get("mu_opgaver"))
            # This will log password in plain text: _LOGGER.debug(self.data)
            return self.async_create_entry(title="Aula", data=self.data)

        return self.async_show_form(
            step_id="user", data_schema=AUTH_SCHEMA, errors=errors
        )

# reconfiguration (options flow), to be implemented
#    @staticmethod
#    @callback
#    def async_get_options_flow(config_entry):
#        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Blueprint config flow options handler."""

    def __init__(self, config_entry):
        """Initialize HACS options flow."""
        self.config_entry = config_entry
        self.options = dict(config_entry.options)

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        _LOGGER.debug("Options......")
        _LOGGER.debug(self.config_entry)
        entity_registry = await async_get(self.hass)
        entries = async_entries_for_config_entry(
            entity_registry, self.config_entry.entry_id
        )
        repo_map = {e.entity_id: e for e in entries}
        for entity_id in repo_map.keys():
            # Unregister from HA
            _LOGGER.debug(entity_id)
            #entity_registry.async_remove(entity_id)
        return await self.async_step_user()

    async def async_step_user(self, user_input=None):
        """Handle a flow initialized by the user."""
        if user_input is not None:
            self.options.update(user_input)
            return await self._update_options()

        return self.async_show_form(
            step_id="user",
            data_schema=AUTH_SCHEMA,
        )

    async def _update_options(self):
        """Update config entry options."""
        return self.async_create_entry(
            title="Aula", data=self.options
        )