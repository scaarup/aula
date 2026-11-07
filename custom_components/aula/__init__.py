from homeassistant.loader import async_get_integration
import asyncio
from homeassistant import config_entries, core
from homeassistant.helpers import entity_registry as er
from .const import (
    DOMAIN,
    STARTUP,
    CONF_MITID_USERNAME,
    CONF_MITID_PASSWORD,
    CONF_MITID_TOKEN,
    CONF_AUTH_METHOD,
    CONF_MITID_IDENTITY,
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_EXPIRES_AT,
    AUTH_METHOD_APP,
    CONF_SCHOOLSCHEDULE,
    CONF_UGEPLAN,
    CONF_MU_OPGAVER,
    CONF_MEETINGS,
)
import logging
from .client import Client

_LOGGER = logging.getLogger(__name__)

# Keys in entry.data that hold the rotating OAuth token set. They are rewritten on
# every token renewal and must never trigger a config entry reload.
TOKEN_KEYS = (CONF_ACCESS_TOKEN, CONF_REFRESH_TOKEN, CONF_TOKEN_EXPIRES_AT)


def _settings_snapshot(entry: config_entries.ConfigEntry) -> dict:
    """Return the part of a config entry that requires a reload when changed."""
    return {
        "data": {k: v for k, v in entry.data.items() if k not in TOKEN_KEYS},
        "options": dict(entry.options),
    }


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
    mitid_token = entry.data.get(CONF_MITID_TOKEN)
    mitid_identity = entry.data.get(CONF_MITID_IDENTITY, 1)

    _LOGGER.info(
        f"Setting up Aula entry for {mitid_username} using method {auth_method}"
    )
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

    # Snapshot of everything except the tokens. options_update_listener compares
    # against it so that persisting a renewed token does not reload the entry.
    # Must be in place before the entry is published to hass.data, because the
    # first token renewal can happen inside client.login() below.
    hass_data["settings_snapshot"] = _settings_snapshot(entry)

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
        mitid_token,
        entry.data.get(CONF_SCHOOLSCHEDULE, True),
        entry.data.get(CONF_UGEPLAN, True),
        entry.data.get(CONF_MU_OPGAVER, True),
        stored_tokens,
        0,  # unread_messages
        mitid_identity,
        hass,  # Pass hass reference for token persistence
        entry,  # Pass config entry for token persistence
        entry.data.get(CONF_MEETINGS, False),
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

    # Unloading a platform that was never forwarded raises and leaves the entry
    # stuck in the non-recoverable FAILED_UNLOAD state, so async_unload_entry
    # must unload exactly what was forwarded here - not what entry.data says
    # *now*, since options updates change entry.data before the reload that
    # calls async_unload_entry. Remember the actual list in runtime storage.
    platforms = ["sensor", "binary_sensor"]
    if _calendar_enabled(entry.data):
        platforms.append("calendar")
    else:
        # Unloading only makes calendar entities unavailable, it doesn't remove
        # them from the registry - so when no calendar is enabled, drop any
        # calendar entities left over from when one was previously on.
        entity_registry = er.async_get(hass)
        for entity_entry in er.async_entries_for_config_entry(
            entity_registry, entry.entry_id
        ):
            if entity_entry.domain == "calendar":
                entity_registry.async_remove(entity_entry.entity_id)
    hass_data["platforms"] = platforms
    await hass.config_entries.async_forward_entry_setups(entry, platforms)
    return True


def _calendar_enabled(data):
    """Return whether any feature that provides calendar entities is on."""
    return data.get(CONF_SCHOOLSCHEDULE, True) or data.get(CONF_MEETINGS, False)


async def async_update_tokens(
    hass: core.HomeAssistant, entry: config_entries.ConfigEntry, tokens: dict
) -> None:
    """Persist a renewed token set to the config entry.

    Aula rotates the refresh token on every renewal, so the rotated token has to be
    written back. Otherwise every restart falls back to the token captured at the
    last interactive MitID login, which the server expires after a couple of months
    and which then forces a manual re-authentication.

    Runs on the event loop; called from executor threads through
    asyncio.run_coroutine_threadsafe(). Writing entry.data fires the update
    listeners, but options_update_listener ignores token-only changes, so this does
    not reload the entry - not even when it is called from inside async_setup_entry's
    own client.login().
    """
    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    if not access_token or not refresh_token:
        _LOGGER.debug("Not persisting an incomplete token set")
        return

    if hass.config_entries.async_get_entry(entry.entry_id) is None:
        # Entry was removed while the refresh was in flight.
        _LOGGER.debug("Config entry is gone, not persisting tokens")
        return

    # async_update_entry is a no-op (and fires no listeners) when nothing changed.
    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            CONF_ACCESS_TOKEN: access_token,
            CONF_REFRESH_TOKEN: refresh_token,
            CONF_TOKEN_EXPIRES_AT: tokens.get("expires_at", 0),
        },
    )
    _LOGGER.debug("Renewed Aula tokens persisted to the config entry")


async def options_update_listener(
    hass: core.HomeAssistant, config_entry: config_entries.ConfigEntry
):
    """Reload the entry when its configuration changed.

    entry.data also carries the OAuth tokens, which are rewritten on every renewal
    (async_update_tokens). Reloading on those would tear the entry down from inside
    its own setup - the very case a renewal at startup hits - and would re-run
    login() on every renewal. Compare against the snapshot taken in
    async_setup_entry and skip token-only updates.
    """
    entry_data = hass.data.get(DOMAIN, {}).get(config_entry.entry_id)
    if entry_data is not None and entry_data.get("settings_snapshot") == (
        _settings_snapshot(config_entry)
    ):
        _LOGGER.debug("Config entry update was tokens only, not reloading")
        return

    await hass.config_entries.async_reload(config_entry.entry_id)


async def async_unload_entry(
    hass: core.HomeAssistant, entry: config_entries.ConfigEntry
) -> bool:
    """Unload a config entry."""
    # Unload exactly the platforms that async_setup_entry actually forwarded,
    # not what entry.data currently says - see the comment there for why.
    stored = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    platforms_to_unload = stored.get("platforms")
    if platforms_to_unload is None:
        platforms_to_unload = ["sensor", "binary_sensor"]
        if _calendar_enabled(entry.data):
            platforms_to_unload.append("calendar")

    unload_ok = await hass.config_entries.async_unload_platforms(entry, platforms_to_unload)

    # Remove options_update_listener.
    if entry.entry_id in hass.data.get(DOMAIN, {}):
        hass.data[DOMAIN][entry.entry_id]["unsub_options_update_listener"]()
        # Remove config entry from domain.
        if unload_ok:
            hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
