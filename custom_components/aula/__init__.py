"""
Based on https://github.com/JBoye/HA-Aula
"""

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.loader import async_get_integration

from .client import Client
from .const import DOMAIN, STARTUP
import logging

_LOGGER = logging.getLogger(__name__)

async def async_setup(hass, config):
    integration = await async_get_integration(hass, DOMAIN)
    _LOGGER.info(STARTUP, integration.version)
    conf = config.get(DOMAIN)
    if conf is None:
        return True

    client  = Client(conf.get(CONF_USERNAME), conf.get(CONF_PASSWORD))
    hass.data[DOMAIN] = {
        "client": client
    }
    
    # Add sensors
    hass.async_create_task(
        hass.helpers.discovery.async_load_platform('sensor', DOMAIN, conf, config)
    )
    # Add calendars, if configured
    CONF_SCHOOLSCHEDULE = "schoolschedule"
    if conf.get(CONF_SCHOOLSCHEDULE):
        client = hass.data[DOMAIN]["client"]
        hass.async_create_task(
            hass.helpers.discovery.async_load_platform('calendar', DOMAIN, conf, config)
        )
    # Initialization was successful.
    return True
