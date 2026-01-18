from homeassistant.loader import async_get_integration
import asyncio
from homeassistant import config_entries, core
from .const import (
    DOMAIN,
    STARTUP,
    CONF_MITID_USERNAME,
    CONF_MITID_PASSWORD,
    CONF_AUTH_METHOD,
    CONF_MITID_IDENTITY,
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_EXPIRES_AT,
    AUTH_METHOD_APP,
    CONF_SCHOOLSCHEDULE,
    CONF_UGEPLAN,
    CONF_MU_OPGAVER,
)
import logging
from .client import Client

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: core.HomeAssistant, entry: config_entries.ConfigEntry
) -> bool:
    """Set up platform from a ConfigEntry."""
    integration = await async_get_integration(hass, DOMAIN)
    _LOGGER.info(STARTUP, integration.version)
    hass.data.setdefault(DOMAIN, {})

    # Extract configuration
    mitid_username = entry.data.get(CONF_MITID_USERNAME)
    auth_method = entry.data.get(CONF_AUTH_METHOD, AUTH_METHOD_APP)
    mitid_password = entry.data.get(CONF_MITID_PASSWORD)
    mitid_identity = entry.data.get(CONF_MITID_IDENTITY, 1)

    _LOGGER.info(f"Setting up Aula entry for {mitid_username}")
    _LOGGER.info(f"Entry data keys: {list(entry.data.keys())}")

    # Extract stored tokens
    stored_tokens = None
    if CONF_ACCESS_TOKEN in entry.data:
        stored_tokens = {
            "access_token": entry.data[CONF_ACCESS_TOKEN],
            "refresh_token": entry.data[CONF_REFRESH_TOKEN],
            "expires_at": entry.data.get(CONF_TOKEN_EXPIRES_AT, 0),
            "token_type": "Bearer",
        }
        _LOGGER.info("Stored tokens found in config entry")
    else:
        _LOGGER.warning(
            f"No stored tokens found in config entry! Keys present: {list(entry.data.keys())}"
        )

    hass_data = dict(entry.data)
    hass_data[CONF_MITID_USERNAME] = mitid_username
    hass_data[CONF_AUTH_METHOD] = auth_method
    hass_data[CONF_MITID_PASSWORD] = mitid_password
    hass_data[CONF_MITID_IDENTITY] = mitid_identity
    hass_data["stored_tokens"] = stored_tokens

    # Registers update listener to update config entry when options are updated.
    unsub_options_update_listener = entry.add_update_listener(options_update_listener)
    # Store a reference to the unsubscribe function to cleanup if an entry is unloaded.
    hass_data["unsub_options_update_listener"] = unsub_options_update_listener
    hass.data[DOMAIN][entry.entry_id] = hass_data

    # Create client with MitID authentication
    client = await hass.async_add_executor_job(
        Client,
        mitid_username,
        auth_method,
        mitid_password,
        entry.data.get(CONF_SCHOOLSCHEDULE, True),
        entry.data.get(CONF_UGEPLAN, True),
        entry.data.get(CONF_MU_OPGAVER, True),
        stored_tokens,
        0,  # unread_messages
        mitid_identity,
        hass,  # Pass hass reference for token persistence
        entry,  # Pass config entry for token persistence
    )
    hass.data[DOMAIN]["client"] = client

    # Perform login/validation
    if not stored_tokens:
        _LOGGER.warning("No stored tokens found, performing authentication")
        await hass.async_add_executor_job(client.login)
    else:
        _LOGGER.info(f"Using stored tokens from config entry")
        # Ensure session is initialized with tokens by calling login which now handles validation
        await hass.async_add_executor_job(client.login)

    # Fetch initial data before setting up platforms
    await hass.async_add_executor_job(client.update_data)

    await hass.config_entries.async_forward_entry_setups(
        entry, ["sensor", "binary_sensor"]
    )
    return True


async def async_update_tokens(
    hass: core.HomeAssistant, entry: config_entries.ConfigEntry, tokens: dict
):
    """Update stored tokens in config entry."""
    new_data = {**entry.data}
    new_data[CONF_ACCESS_TOKEN] = tokens["access_token"]
    new_data[CONF_REFRESH_TOKEN] = tokens["refresh_token"]
    new_data[CONF_TOKEN_EXPIRES_AT] = tokens.get("expires_at", 0)

    hass.config_entries.async_update_entry(entry, data=new_data)
    _LOGGER.debug("Tokens updated in config entry")


async def options_update_listener(
    hass: core.HomeAssistant, config_entry: config_entries.ConfigEntry
):
    """Handle options update."""
    await hass.config_entries.async_reload(config_entry.entry_id)


async def async_unload_entry(
    hass: core.HomeAssistant, entry: config_entries.ConfigEntry
) -> bool:
    """Unload a config entry."""
    # Unload all platforms that may have been set up
    platforms_to_unload = ["sensor", "binary_sensor", "calendar"]
    unload_ok = await hass.config_entries.async_unload_platforms(entry, platforms_to_unload)

    # Remove options_update_listener.
    if entry.entry_id in hass.data.get(DOMAIN, {}):
        hass.data[DOMAIN][entry.entry_id]["unsub_options_update_listener"]()
        # Remove config entry from domain.
        if unload_ok:
            hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
