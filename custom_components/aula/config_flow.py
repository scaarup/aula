import logging
import asyncio
import concurrent.futures
from typing import Any, Dict, Optional

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers import network
import homeassistant.helpers.config_validation as cv
import voluptuous as vol

from .const import (
    CONF_SCHOOLSCHEDULE,
    CONF_UGEPLAN,
    CONF_MU_OPGAVER,
    CONF_TEACHER_NAME_DISPLAY,
    TEACHER_NAME_INITIALS,
    TEACHER_NAME_FULL,
    TEACHER_NAME_FIRST_NAME_INITIALS,
    resolve_teacher_name_display,
    CONF_MITID_USERNAME,
    CONF_MITID_PASSWORD,
    CONF_MITID_TOKEN,
    CONF_AUTH_METHOD,
    CONF_MITID_USE_TOKEN,
    CONF_MITID_IDENTITY,
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_EXPIRES_AT,
    AUTH_METHOD_APP,
    AUTH_METHOD_TOKEN,
    DOMAIN,
)
from .aula_login_client.client import AulaLoginClient
from .aula_login_client.exceptions import AulaAuthenticationError
from .views import AulaAuthView, AulaAuthStatusView, AulaAuthSelectIdentityView

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_MITID_USERNAME): cv.string,
        vol.Optional(CONF_MITID_USE_TOKEN, default=False): cv.boolean,
        vol.Optional(CONF_SCHOOLSCHEDULE, default=True): cv.boolean,
        vol.Optional(CONF_UGEPLAN, default=True): cv.boolean,
        vol.Optional(CONF_MU_OPGAVER, default=True): cv.boolean,
    }
)

TOKEN_CREDENTIALS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_MITID_PASSWORD): cv.string,
        vol.Required(CONF_MITID_TOKEN): cv.string,
    }
)

TEACHER_NAME_DISPLAY_OPTIONS = {
    TEACHER_NAME_INITIALS: "Teacher initials",
    TEACHER_NAME_FULL: "Teacher full name",
    TEACHER_NAME_FIRST_NAME_INITIALS: "Teacher first name (initials)",
}

TEACHER_NAME_DISPLAY_SCHEMA = vol.Schema(
    {
        vol.Optional(
            CONF_TEACHER_NAME_DISPLAY, default=TEACHER_NAME_INITIALS
        ): vol.In(TEACHER_NAME_DISPLAY_OPTIONS),
    }
)


class AulaCustomConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Aula Custom config flow with multi-step MitID authentication."""

    VERSION = 2

    def __init__(self):
        """Initialize the config flow."""
        self._mitid_username = None
        self._auth_method = None
        self._mitid_password = None
        self._mitid_token = None
        self._feature_flags = {}
        self._auth_client = None
        self._tokens = None
        self._auth_error = None
        self._reauth_entry = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return OptionsFlowHandler()

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None):
        """Handle initial user input."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            use_token = user_input.get(CONF_MITID_USE_TOKEN, False)
            self._mitid_username = user_input[CONF_MITID_USERNAME]
            self._auth_method = AUTH_METHOD_TOKEN if use_token else AUTH_METHOD_APP
            self._feature_flags = {
                CONF_SCHOOLSCHEDULE: user_input.get(CONF_SCHOOLSCHEDULE, True),
                CONF_UGEPLAN: user_input.get(CONF_UGEPLAN, True),
                CONF_MU_OPGAVER: user_input.get(CONF_MU_OPGAVER, True),
                CONF_MITID_USE_TOKEN: use_token,
            }

            if self._feature_flags[CONF_SCHOOLSCHEDULE]:
                return await self.async_step_teacher_name_display()
            if use_token:
                return await self.async_step_token_credentials()
            return await self.async_step_authenticate()

        return self.async_show_form(
            step_id="user", data_schema=USER_SCHEMA, errors=errors
        )

    async def async_step_teacher_name_display(
        self, user_input: Optional[Dict[str, Any]] = None
    ):
        """Ask how to display teacher names (only shown when schoolschedule is enabled)."""
        if user_input is not None:
            self._feature_flags[CONF_TEACHER_NAME_DISPLAY] = user_input[
                CONF_TEACHER_NAME_DISPLAY
            ]
            if self._auth_method == AUTH_METHOD_TOKEN:
                return await self.async_step_token_credentials()
            return await self.async_step_authenticate()

        return self.async_show_form(
            step_id="teacher_name_display", data_schema=TEACHER_NAME_DISPLAY_SCHEMA
        )

    async def async_step_token_credentials(self, user_input: Optional[Dict[str, Any]] = None):
        """Handle token device credentials (only shown when use_token is True)."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            self._mitid_password = user_input[CONF_MITID_PASSWORD]
            self._mitid_token = user_input[CONF_MITID_TOKEN]
            return await self.async_step_authenticate()

        return self.async_show_form(
            step_id="token_credentials", data_schema=TOKEN_CREDENTIALS_SCHEMA, errors=errors
        )

    async def async_step_authenticate(
        self, user_input: Optional[Dict[str, Any]] = None
    ):
        """Handle MitID authentication with progress display."""
        # Check if session exists
        session_data = None
        if (
            DOMAIN in self.hass.data
            and "auth_sessions" in self.hass.data[DOMAIN]
            and self.flow_id in self.hass.data[DOMAIN]["auth_sessions"]
        ):
            session_data = self.hass.data[DOMAIN]["auth_sessions"][self.flow_id]

        # Determine if we should start/restart authentication
        # Start if:
        # 1. No session exists (first run)
        # 2. Session exists but has error AND user clicked submit (retry)
        should_start = session_data is None or (
            user_input is not None and session_data.get("error")
        )

        if should_start:
            # Initialize shared session storage
            self.hass.data.setdefault(DOMAIN, {})
            self.hass.data[DOMAIN].setdefault("auth_sessions", {})

            # Create authentication client
            # Only create if not already created (to avoid restarting on reload)
            if not self._auth_client:
                # Register views
                self.hass.http.register_view(AulaAuthView(self.hass))
                self.hass.http.register_view(AulaAuthStatusView(self.hass))
                self.hass.http.register_view(AulaAuthSelectIdentityView(self.hass))

            self._auth_client = AulaLoginClient(
                mitid_username=self._mitid_username,
                mitid_password=self._mitid_password,
                mitid_token=self._mitid_token,
                auth_method=self._auth_method,
                verbose=True,
                debug=True,
            )

            # Setup session data
            session_data = {
                "client": self._auth_client,
                "status_message": "Open your MitID app now...",
                "completed": False,
                "error": None,
                "identity_future": None,
                "available_identities": None,
            }
            self.hass.data[DOMAIN]["auth_sessions"][self.flow_id] = session_data

            # Set up identity selector callback
            def identity_selector(identities):
                """Callback to select identity via web view."""
                session_data["available_identities"] = identities
                session_data["status_message"] = "Please select an identity"

                # Create a thread-safe future to wait for selection
                future = concurrent.futures.Future()
                session_data["identity_future"] = future

                try:
                    # Block until the view sets the result
                    return future.result(timeout=300)
                except Exception as e:
                    _LOGGER.error("Identity selection timed out or failed: %s", e)
                    raise

            self._auth_client.identity_selector = identity_selector

            # Start authentication task
            self.hass.async_create_task(self._authenticate_async(session_data))
            try:
                base_url = network.get_url(
                    self.hass,
                    require_current_request=True,
                )
            except network.NoURLAvailableError:
                base_url = network.get_url(self.hass)

            return self.async_external_step(
                step_id="authenticate",
                url=f"{base_url.rstrip('/')}/api/aula/auth/{self.flow_id}",
            )

        if session_data.get("completed"):
            # Store tokens before completing
            self._tokens = session_data.get("tokens")
            if not self._tokens:
                _LOGGER.error("Tokens not found in completed session")
                return self.async_external_step_done(next_step_id="reauth_error")

            # Schedule delayed cleanup of session data
            self.hass.async_create_task(self._delayed_cleanup(self.flow_id))

            _LOGGER.info("Tokens validated, proceeding to complete step")

            # Transition to completion step (external step can only go to external_step_done)
            return self.async_external_step_done(next_step_id="complete")

        if session_data.get("error"):
            return self.async_external_step_done(next_step_id="reauth_error")

        # Not done yet
        return self.async_external_step(
            step_id="authenticate",
            url=f"/api/aula/auth/{self.flow_id}",
        )

    async def async_step_complete(self, user_input=None):
        """Complete the authentication flow by creating or updating the config entry."""
        _LOGGER.info("Completing authentication flow")

        # Build config entry data
        data = {
            CONF_MITID_USERNAME: self._mitid_username,
            CONF_AUTH_METHOD: self._auth_method,
            CONF_ACCESS_TOKEN: self._tokens["access_token"],
            CONF_REFRESH_TOKEN: self._tokens["refresh_token"],
            CONF_TOKEN_EXPIRES_AT: self._tokens.get("expires_at", 0),
            **self._feature_flags,
        }

        _LOGGER.info(f"Config entry data keys: {list(data.keys())}")

        # Check if this is a reauth/reconfigure flow
        if self._reauth_entry:
            # Update existing entry with new tokens
            _LOGGER.info("Updating existing entry with new tokens")
            # Updating the entry triggers options_update_listener (__init__.py),
            # which reloads it - an explicit reload here would race that reload.
            self.hass.config_entries.async_update_entry(
                self._reauth_entry, data=data
            )
            return self.async_abort(reason="reauth_successful")
        else:
            # Create new entry
            _LOGGER.info(f"Creating new config entry for {self._mitid_username}")
            return self.async_create_entry(
                title=f"Aula ({self._mitid_username})", data=data
            )

    async def _authenticate_async(self, session_data):
        """Background task for authentication."""
        try:
            _LOGGER.info(
                "Starting MitID authentication for %s using %s",
                self._mitid_username,
                self._auth_client.auth_method,
            )

            # Start monitoring task
            monitor_task = self.hass.async_create_task(
                self._monitor_client_status(session_data)
            )

            # Run authentication in executor (it's synchronous)
            result = await self.hass.async_add_executor_job(
                self._auth_client.authenticate
            )

            monitor_task.cancel()

            if result.get("success"):
                session_data["tokens"] = result.get("tokens")
                session_data["completed"] = True
                session_data["status_message"] = "Authentication successful!"
                _LOGGER.info("Authentication successful")
            else:
                error_msg = result.get("error", "Unknown error")
                session_data["error"] = error_msg
                _LOGGER.error("Authentication failed: %s", error_msg)

        except Exception as err:
            _LOGGER.error("Authentication error: %s", err)
            session_data["error"] = str(err)

        # Advance the flow
        self.hass.async_create_task(
            self.hass.config_entries.flow.async_configure(flow_id=self.flow_id)
        )

    async def async_step_reauth_error(self, user_input=None):
        """Display error and allow retry."""
        if user_input is not None:
            return await self.async_step_authenticate(user_input)

        return self.async_show_form(
            step_id="reauth_error",
            errors={"base": "auth_failed"},
            description_placeholders={"auth_url": f"/api/aula/auth/{self.flow_id}"},
        )

    async def _monitor_client_status(self, session_data):
        """Monitor client status and update session data."""
        client = session_data["client"]
        while True:
            try:
                mitid_client = client.get_mitid_client()
                if mitid_client and hasattr(mitid_client, "status_message"):
                    # Only update if not in identity selection mode
                    if not session_data.get("available_identities"):
                        session_data["status_message"] = mitid_client.status_message
            except Exception:
                pass
            await asyncio.sleep(1)

    async def _delayed_cleanup(self, flow_id):
        """Cleanup session data after a delay."""
        await asyncio.sleep(60)
        if (
            DOMAIN in self.hass.data
            and "auth_sessions" in self.hass.data[DOMAIN]
            and flow_id in self.hass.data[DOMAIN]["auth_sessions"]
        ):
            self.hass.data[DOMAIN]["auth_sessions"].pop(flow_id, None)

    async def async_step_reauth(self, entry_data):
        """Handle reauth flow."""
        _LOGGER.info("Starting reauth flow")
        # Store the entry being reauthenticated
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )

        # Store existing configuration
        auth_method = self._reauth_entry.data.get(CONF_AUTH_METHOD, AUTH_METHOD_APP)
        _LOGGER.info("Aula reauth stored auth_method data was {auth_method}")
        if self._reauth_entry.data.get(CONF_MITID_USE_TOKEN, False) is True:
            auth_method = AUTH_METHOD_TOKEN

        self._mitid_username = self._reauth_entry.data.get(CONF_MITID_USERNAME)
        # Reauth will require token again
        self._auth_method = auth_method
        self._mitid_password = None
        self._feature_flags = {
            CONF_SCHOOLSCHEDULE: self._reauth_entry.data.get(CONF_SCHOOLSCHEDULE, True),
            CONF_UGEPLAN: self._reauth_entry.data.get(CONF_UGEPLAN, True),
            CONF_MU_OPGAVER: self._reauth_entry.data.get(CONF_MU_OPGAVER, True),
            CONF_TEACHER_NAME_DISPLAY: resolve_teacher_name_display(self._reauth_entry.data),
        }

        # Start authentication process
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Confirm reauth and start authentication."""
        if user_input is not None:
            # Clear any stale session data from previous auth attempts
            if (
                DOMAIN in self.hass.data
                and "auth_sessions" in self.hass.data[DOMAIN]
                and self.flow_id in self.hass.data[DOMAIN]["auth_sessions"]
            ):
                self.hass.data[DOMAIN]["auth_sessions"].pop(self.flow_id, None)
                _LOGGER.debug("Cleared stale auth session for flow %s", self.flow_id)

            # Proceed to authentication
            return await self.async_step_authenticate()

        return self.async_show_form(
            step_id="reauth_confirm",
            description_placeholders={
                "username": self._mitid_username,
                "password": None,
                "token": None,
            },
        )

    async def async_step_reconfigure(self, user_input=None):
        """Handle reconfiguration (manual re-authentication)."""
        _LOGGER.info("Starting reconfigure flow")
        # Store the entry being reconfigured
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        # Store existing configuration
        auth_method = self._reauth_entry.data.get(CONF_AUTH_METHOD, AUTH_METHOD_APP)
        _LOGGER.info("Aula reconfig stored auth_method data was {auth_method}")

        self._mitid_username = self._reauth_entry.data.get(CONF_MITID_USERNAME)
        self._auth_method = auth_method
        self._mitid_password = None
        self._mitid_token = None
        self._feature_flags = {
            CONF_SCHOOLSCHEDULE: self._reauth_entry.data.get(CONF_SCHOOLSCHEDULE, True),
            CONF_UGEPLAN: self._reauth_entry.data.get(CONF_UGEPLAN, True),
            CONF_MU_OPGAVER: self._reauth_entry.data.get(CONF_MU_OPGAVER, True),
            CONF_TEACHER_NAME_DISPLAY: resolve_teacher_name_display(self._reauth_entry.data),
        }

        # Start authentication process
        return await self.async_step_reauth_confirm()


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Aula integration (feature toggles)."""

    def __init__(self):
        """Initialize options flow."""
        self._pending_updates = {}

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        return await self.async_step_options()

    async def async_step_options(self, user_input=None):
        """Show and handle the feature-toggle options form."""
        if user_input is not None:
            self._pending_updates = dict(user_input)
            if self._pending_updates.get(CONF_SCHOOLSCHEDULE):
                return await self.async_step_teacher_name_display()
            return self._save_options()

        current = self.config_entry.data
        options_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SCHOOLSCHEDULE,
                    default=current.get(CONF_SCHOOLSCHEDULE, True),
                ): cv.boolean,
                vol.Optional(
                    CONF_UGEPLAN, default=current.get(CONF_UGEPLAN, True)
                ): cv.boolean,
                vol.Optional(
                    CONF_MU_OPGAVER, default=current.get(CONF_MU_OPGAVER, True)
                ): cv.boolean,
            }
        )
        return self.async_show_form(step_id="options", data_schema=options_schema)

    async def async_step_teacher_name_display(self, user_input=None):
        """Show and handle the teacher-name display select (only when schoolschedule is on)."""
        if user_input is not None:
            self._pending_updates[CONF_TEACHER_NAME_DISPLAY] = user_input[
                CONF_TEACHER_NAME_DISPLAY
            ]
            return self._save_options()

        default = resolve_teacher_name_display(self.config_entry.data)
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_TEACHER_NAME_DISPLAY, default=default
                ): vol.In(TEACHER_NAME_DISPLAY_OPTIONS),
            }
        )
        return self.async_show_form(step_id="teacher_name_display", data_schema=schema)

    def _save_options(self):
        # Updating the entry triggers options_update_listener (__init__.py),
        # which reloads it - an explicit reload here would race that reload.
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data={**self.config_entry.data, **self._pending_updates},
        )
        return self.async_create_entry(title="", data={})
