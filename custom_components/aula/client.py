"""
Aula client implementation
Based on https://github.com/JBoye/HA-Aula

This is the main orchestrator that composes functionality from:
- aula_auth.py: Authentication, session management, browser automation
- widgets.py: Widget detection, token management, institution types
- presence.py: Presence templates, weekly presence, closed days
- posts.py: Posts/news fetching and parsing
- mail.py: Mail threads fetching and parsing
"""

import datetime
import json
import logging
import os
import re
import time

import requests
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .const import (
    CICERO_API,
    MEEBOOK_API,
    MIN_UDDANNELSE_API,
    OAUTH_CLIENT_ID,
    OAUTH_TOKEN_URL,
    SYSTEMATIC_API,
)
from .mail import MailMixin
from .minuddannelse import MinUddannelse
from .posts import PostsMixin
from .presence import PresenceMixin
from .widgets import WidgetsMixin
from .widget_auth import widget_guardian

_LOGGER = logging.getLogger(__name__)


def _safe_json(response, context="API call"):
    """Parse JSON from response, raising on non-200 status or empty body."""
    if response.status_code != 200:
        raise Exception(f"{context} returned status {response.status_code}")
    return response.json()


class Client(WidgetsMixin, PresenceMixin, PostsMixin, MailMixin):
    def _resolve_api_url(self):
        """Resolve a working Aula API base URL.

        Tries configured API version first, then a small set of nearby versions.
        """
        from .const import API, API_VERSION

        preferred = str(API_VERSION)
        candidates = [preferred, "23", "24", "25", "21"]
        # Keep order but remove duplicates.
        candidates = list(dict.fromkeys(candidates))

        for version in candidates:
            apiurl = API + version + "/"
            try:
                # Use getProfileContext as probe — it's the first real post-auth call
                # and works on v23 with portalrole=guardian.
                # Auth via ?access_token= URL param (matches iOS app, not Bearer header).
                probe_url = (
                    apiurl + "?method=profiles.getProfileContext&portalrole=guardian"
                )
                if self._device_id:
                    probe_url += f"&deviceId={self._device_id}"
                if self._access_token:
                    probe_url += f"&access_token={self._access_token}"
                response = self._session.get(
                    probe_url,
                    verify=True,
                    timeout=10,
                )
                if response.status_code == 200:
                    data = response.json()
                    if data.get("status", {}).get("message") == "OK":
                        if version != preferred:
                            _LOGGER.warning(
                                "Configured Aula API version v%s unavailable, using v%s",
                                preferred,
                                version,
                            )
                        return apiurl
            except Exception as exc:
                _LOGGER.debug("API version probe v%s failed: %s", version, exc)

        fallback_url = API + preferred + "/"
        _LOGGER.warning(
            "Could not verify a working Aula API version, falling back to configured v%s",
            preferred,
        )
        return fallback_url

    def _setup_post_login(self):
        """Setup API access after successful login.

        Mirrors the iOS app post-auth sequence from sniffer capture:
        1. profiles.getProfileContext&portalrole=guardian  → widget config
        2. profiles.getprofilesbylogin&portalRoles[]=guardian → children/profiles
        3. notifications.registerDevice (POST)             → device registration
        """
        try:
            self.apiurl = self._resolve_api_url()
            _LOGGER.debug(f"Using API at {self.apiurl}")

            # Step 1: getProfileContext — widget config (first call in iOS post-auth)
            try:
                profile_context = self._session.get(
                    self._make_api_url(
                        "profiles.getProfileContext", portalrole="guardian"
                    ),
                    verify=True,
                    timeout=10,
                ).json()

                if (
                    profile_context
                    and "data" in profile_context
                    and profile_context["data"]
                    and "institutionProfile" in profile_context["data"]
                ):
                    _LOGGER.info("Successfully retrieved profile context")
                else:
                    _LOGGER.warning("Profile context response missing expected data")

            except Exception as e:
                _LOGGER.error(f"Failed to get profile context: {e}")

            # Step 2: getprofilesbylogin — children/profiles (second call in iOS post-auth)
            # Note: method name is all-lowercase and requires portalRoles[]=guardian
            if not (hasattr(self, "_profiles") and self._profiles):
                ver = self._session.get(
                    self._make_api_url(
                        "profiles.getprofilesbylogin",
                        portalRoles=["guardian"],
                    ),
                    verify=True,
                    timeout=10,
                )

                if ver.status_code == 200:
                    try:
                        response_data = ver.json()
                        if (
                            "data" in response_data
                            and "profiles" in response_data["data"]
                        ):
                            self._profiles = response_data["data"]["profiles"]
                            _LOGGER.info(f"Successfully connected to API {self.apiurl}")
                        else:
                            raise ConfigEntryNotReady(
                                "API response missing expected data structure"
                            )
                    except ValueError as e:
                        raise ConfigEntryNotReady(
                            f"Invalid JSON response from API: {e}"
                        )
                else:
                    raise ConfigEntryNotReady(
                        f"API returned unexpected status: {ver.status_code}"
                    )

            # Step 3: notifications.registerDevice (POST) — third call in iOS post-auth
            try:
                self._session.post(
                    self._make_api_url("notifications.registerDevice"),
                    verify=True,
                    timeout=10,
                )
                _LOGGER.debug("Device registered with notifications service")
            except Exception as e:
                _LOGGER.warning(f"notifications.registerDevice failed (non-fatal): {e}")

        except ConfigEntryNotReady:
            raise
        except Exception as e:
            _LOGGER.error(f"Post-login setup failed: {e}")
            raise

        _LOGGER.debug(
            "Config - schoolschedule: "
            + str(self._schoolschedule)
            + ", config - ugeplaner: "
            + str(self._ugeplan)
        )

    huskeliste = {}
    presence = {}
    presence_templates = {}
    presence_templates_next = {}
    closed_days = {}
    weekly_presence_current = {}
    weekly_presence_next = {}
    ugep_attr = {}
    ugepnext_attr = {}
    widgets = {}
    tokens = {}
    loaned_books = {}
    forloebthisweek = {}
    forloebnext = {}
    ugenotethisweek = {}
    ugenotenextweek = {}
    posts = {}
    posts_by_child = {}

    def __init__(
        self,
        schoolschedule,
        ugeplan,
        stored_tokens=None,
        token_persist_callback=None,
        hass=None,
        config_entry=None,
        device_id=None,
    ):
        self._session = None
        self._last_data_update = 0

        # OAuth2 token storage (from secret storage)
        self._stored_tokens = stored_tokens or {}
        self._token_persist_callback = token_persist_callback
        self._access_token = None
        self._hass = hass
        self._config_entry = config_entry
        self._device_id = device_id

        # Use Home Assistant config dir for data files (not CWD)
        self._data_dir = hass.config.path() if hass else os.getcwd()

        self._schoolschedule = schoolschedule
        self._ugeplan = ugeplan
        # Widget-based features now auto-detected from API
        self._bibliotek = None  # Auto-detected
        self.library_available = False
        self._minUddannelseForloeb = None  # Auto-detected
        self._minUddannelseOpgaveListe = None  # Auto-detected
        self._minUddannelseUgeNote = None  # Auto-detected
        self._minUddannelse = MinUddannelse(
            None,
            None,
            None,  # Will be set dynamically based on widget availability
        )
        # Initialize attributes that update_data populates, so they exist even if auth fails
        self._children = []
        self._childnames = {}
        self._childids = []
        self._childuserids = []
        self._institutions = {}
        self._institution_types = {}
        self._institutionProfiles = []
        self._institutionProfileIdsList = []
        self._daily_overview = {}

    def _ensure_token_auth(self):
        """Ensure we have a valid access token for API calls.

        Token Authentication Strategy:
        1. Load access_token, refresh_token, expires_at from storage
        2. Check if access_token still valid (5 min buffer before actual expiry)
        3. If expired: Use refresh_token to get new access_token (OAuth2 grant)
        4. If no refresh_token: Raise ConfigEntryAuthFailed (user must reauth)
        5. Store self._access_token for URL access_token API calls

        Device ID:
        - Always included in API URL params (self._device_id)
        - Required in every API call
        - Never expires, never refreshed
        """
        if not self._session:
            self._session = requests.Session()
            self._session.headers.update(
                {
                    "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/113.0.0.0 Mobile Safari/537.36",
                    "Accept": "application/json",
                }
            )

        # ──────────────────────────────────────────────────────────────────
        # STEP 1: Load tokens from storage (Set on __init__ from config entry)
        # ──────────────────────────────────────────────────────────────────
        access_token = self._stored_tokens.get("access_token")
        refresh_token = self._stored_tokens.get("refresh_token")
        # Support both legacy and current expiry keys.
        expires_at = self._stored_tokens.get(
            "expires_at", self._stored_tokens.get("token_expires_at", 0)
        )

        # ──────────────────────────────────────────────────────────────────
        # STEP 2: Check if access_token still valid (with 5 min buffer)
        # ──────────────────────────────────────────────────────────────────
        # Why 5 min buffer? Avoid using token that's about to expire mid-API-call
        if access_token and time.time() < expires_at - 300:
            # ✅ Token still valid, use it
            self._access_token = access_token
            _LOGGER.debug(
                "Using existing access token (expires in %ds)",
                int(expires_at - time.time()),
            )
            return True

        # ──────────────────────────────────────────────────────────────────
        # STEP 3: Access token expired, try refresh (simple HTTP POST)
        # ──────────────────────────────────────────────────────────────────
        # No user interaction needed - refresh_token is long-lived (~30 days)
        if refresh_token:
            _LOGGER.debug("Access token expired, attempting refresh...")
            try:
                # ──────────────────────────────────────────────────────────────────
                # STEP 3a: POST to OAuth token endpoint with refresh_token grant
                # ──────────────────────────────────────────────────────────────────
                # No user interaction needed - refresh_token is long-lived (~30 days)
                # Returns new access_token with fresh expiry
                r = self._session.post(
                    OAUTH_TOKEN_URL,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "client_id": OAUTH_CLIENT_ID,
                    },
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Accept": "application/json",
                    },
                    timeout=30,
                )

                # ──────────────────────────────────────────────────────────────────
                # STEP 3b: Process refresh response
                # ──────────────────────────────────────────────────────────────────
                if r.status_code == 200:
                    new = r.json()

                    # Update stored tokens with fresh values
                    self._stored_tokens["access_token"] = new["access_token"]

                    # Refresh token may be rotated (include if in response)
                    if "refresh_token" in new:
                        self._stored_tokens["refresh_token"] = new["refresh_token"]

                    # Calculate new expiry timestamp
                    if "expires_in" in new:
                        expires_ts = time.time() + new["expires_in"]
                        self._stored_tokens["expires_at"] = expires_ts
                        self._stored_tokens["token_expires_at"] = expires_ts

                    # Device ID never changes - keep it in storage
                    if self._device_id:
                        self._stored_tokens["device_id"] = self._device_id

                    # Set in-use access token
                    self._access_token = new["access_token"]

                    # ──────────────────────────────────────────────────────────────────
                    # STEP 3c: Persist refreshed tokens to Home Assistant storage
                    # ──────────────────────────────────────────────────────────────────
                    # This ensures tokens survive process restarts
                    if self._token_persist_callback:
                        cb = self._token_persist_callback
                        # Running in a SyncWorker thread — no event loop here.
                        # Use run_coroutine_threadsafe to safely call async callbacks.
                        import asyncio

                        if asyncio.iscoroutinefunction(cb):
                            if self._hass and self._hass.loop:
                                asyncio.run_coroutine_threadsafe(
                                    cb(self._stored_tokens), self._hass.loop
                                )
                            else:
                                _LOGGER.warning(
                                    "Cannot persist tokens: no event loop available"
                                )
                        else:
                            cb(self._stored_tokens)

                    _LOGGER.info(
                        "Access token refreshed (expires in %ss)",
                        new.get("expires_in", "?"),
                    )
                    return True
                else:
                    # Refresh failed (bad grant, expired refresh token, etc.)
                    _LOGGER.warning(
                        "Token refresh failed: HTTP %s. "
                        "Refresh token may have expired. User must reauth.",
                        r.status_code,
                    )
            except Exception as e:
                _LOGGER.error("Token refresh error: %s", e)

        # ──────────────────────────────────────────────────────────────────
        # STEP 4: All auth attempts failed - require user reauth
        # ──────────────────────────────────────────────────────────────────
        # Full re-authentication requires user interaction
        # Cannot be done automatically - device_id, access_token, and refresh_token
        # are user-specific and time-sensitive.
        raise ConfigEntryAuthFailed(
            "Aula session udløbet. Gå til Indstillinger → Integrationer → Aula → Konfigurer for at logge ind igen."
        )

    def _make_api_url(self, method, **params):
        """Build API URL with access_token and device_id as URL query params.

        The Aula API authenticates via ?access_token= in the URL (not Bearer header).
        This matches the iOS app behaviour captured in the sniffer JSONL.

        Example output:
            https://www.aula.dk/api/v23/?method=profiles.getProfileContext
            &deviceId=IOS-private-D22D4696-6913-43A1-A7AB-592903951E6C
            &access_token=eyJ0eXAi...
            &portalrole=guardian
        """
        url = self.apiurl + f"?method={method}"

        # Add deviceId if present
        if self._device_id and "deviceId" not in params:
            url += f"&deviceId={self._device_id}"

        # Embed access_token in URL (Aula API requires this, not Bearer header)
        if self._access_token:
            url += f"&access_token={self._access_token}"

        # Add additional parameters (filters, options, etc.)
        for key, val in params.items():
            if isinstance(val, list):
                # Array params: key[]=val1&key[]=val2
                for v in val:
                    url += f"&{key}[]={v}"
            else:
                # Single params: key=val
                url += f"&{key}={val}"
        return url

    def _data_path(self, filename):
        """Return the full path for a data file under HA config dir."""
        return os.path.join(self._data_dir, filename)

    def _auth_headers(self):
        """Return compatibility headers for mixins.

        Aula API auth is performed via URL query params in _make_api_url().
        This helper is kept to avoid breaking mixins that still pass headers.
        """
        return {"Accept": "application/json"}

    def update_data(self):
        # Rate limiting: prevent multiple rapid data fetches within 30 seconds
        current_time = time.time()
        if current_time - self._last_data_update < 30:  # 30 seconds
            _LOGGER.debug(
                f"Data update rate limited, using cached result (last update: {current_time - self._last_data_update:.1f}s ago)"
            )
            return

        self._last_data_update = current_time
        _LOGGER.debug("Starting data update cycle...")

        # Clear cached widget tokens so they are re-fetched fresh each cycle
        self.tokens = {}

        # Ensure we have a valid access token
        self._ensure_token_auth()  # raises ConfigEntryAuthFailed if no valid token

        # Ensure post-login setup is done
        if (
            not hasattr(self, "apiurl")
            or not hasattr(self, "_profiles")
            or not self._profiles
        ):
            self._setup_post_login()

        # Verify API access and refresh profiles data
        # Uses getprofilesbylogin (lowercase) with portalRoles[]=guardian
        # — matches the iOS app's second post-auth call exactly
        is_logged_in = False
        if (
            self._session
            and hasattr(self, "apiurl")
            and hasattr(self, "_profiles")
            and self._profiles
        ):
            try:
                api_url = self._make_api_url(
                    "profiles.getprofilesbylogin",
                    portalRoles=["guardian"],
                )

                response = self._session.get(
                    api_url,
                    verify=True,
                    timeout=10,
                )
                data = _safe_json(response, "profiles.getprofilesbylogin")
                is_logged_in = data.get("status", {}).get("message") == "OK"
                if is_logged_in:
                    # Keep profiles fresh
                    self._profiles = data.get("data", {}).get(
                        "profiles", self._profiles
                    )
            except Exception as e:
                _LOGGER.warning(f"Failed to test API access: {e}")
                is_logged_in = False

        _LOGGER.debug("is_logged_in? " + str(is_logged_in))

        if not is_logged_in:
            _LOGGER.error("API access test failed - authentication may have expired")
            raise ConfigEntryNotReady(
                "API access denied. Please reconfigure the integration."
            )

        self._childnames = {}
        self._institutions = {}
        self._institution_types = {}
        self._childuserids = []
        self._childids = []
        self._children = []
        self._institutionProfiles = []
        self._institutionProfileIdsList = []

        for profile in self._profiles:
            for child in profile["children"]:
                self._childnames[child["id"]] = child["name"]
                self._institutions[child["id"]] = child["institutionProfile"][
                    "institutionName"
                ]

                # Detect institution type
                institution_type = self._detect_institution_type(
                    child["institutionProfile"]
                )
                self._institution_types[child["id"]] = institution_type

                self._children.append(child)
                self._childids.append(str(child["id"]))
                self._childuserids.append(str(child["userId"]))

                # Add child IDs to institution profile IDs list for posts filtering
                child_id = str(child["id"])
                if child_id not in self._institutionProfileIdsList:
                    self._institutionProfileIdsList.append(child_id)

            for institutioncode in profile["institutionProfiles"]:
                if (
                    str(institutioncode["institutionCode"])
                    not in self._institutionProfiles
                ):
                    self._institutionProfiles.append(
                        str(institutioncode["institutionCode"])
                    )
                # Add institution profile IDs for posts filtering
                institution_profile_id = str(institutioncode["id"])
                if institution_profile_id not in self._institutionProfileIdsList:
                    self._institutionProfileIdsList.append(institution_profile_id)

        # Debug child mapping after initialization
        _LOGGER.debug(
            f"Initialized {len(self._children)} children for {len(self._institutionProfiles)} institutions"
        )

        self._daily_overview = {}
        for i, child in enumerate(self._children):
            response = self._session.get(
                self._make_api_url(
                    "presence.getDailyOverview",
                    childIds=[str(child["id"])],
                ),
                verify=True,
            )
            data = _safe_json(
                response, f"presence.getDailyOverview child={child['id']}"
            )
            if len(data["data"]) > 0:
                self.presence[str(child["id"])] = 1
                self._daily_overview[str(child["id"])] = data["data"][0]
            else:
                _LOGGER.warn(
                    "Unable to retrieve presence data from Aula from child with id "
                    + str(child["id"])
                    + ". Some data will be missing from sensor entities."
                )
                self.presence[str(child["id"])] = 0
        _LOGGER.debug("Child ids and presence data status: " + str(self.presence))

        # Initialize widgets early so feature flags are set before widget sections
        if len(self.widgets) == 0:
            self.get_widgets()

        # Weekly Presence (Komme og Gå):
        self._get_weekly_presence()

        # Presence Templates (Weekly Schedule):
        self._get_presence_templates()

        # Closed Days (Institution holidays):
        self._get_closed_days()

        # Messages:
        try:
            _LOGGER.debug(
                "OLD MESSAGES: About to call messaging.getThreads (page 0 only)..."
            )
            mesres = self._session.get(
                self._make_api_url(
                    "messaging.getThreads",
                    sortOn="date",
                    orderDirection="desc",
                    page=0,
                ),
                verify=True,
            )
            _LOGGER.debug(f"OLD MESSAGES: Response status: {mesres.status_code}")
            self.unread_messages = 0
            unread = 0
            self.message = {}
            mesdata = _safe_json(mesres, "messaging.getThreads")
            for mes in mesdata["data"]["threads"]:
                if not mes["read"]:
                    unread = 1
                    threadid = mes["id"]
                    break
            _LOGGER.debug(f"OLD MESSAGES: Found unread messages: {unread}")
        except Exception as e:
            _LOGGER.error(f"OLD MESSAGES: Failed to fetch messages: {e}")
            self.unread_messages = 0
            unread = 0
            self.message = {}

        # Continue with unread message details (also within try-catch)
        try:
            if unread == 1:
                threadres = self._session.get(
                    self._make_api_url(
                        "messaging.getMessagesForThread",
                        threadId=threadid,
                        page=0,
                    ),
                    verify=True,
                )
                threaddata = _safe_json(threadres, "messaging.getMessagesForThread")
                if threaddata["status"]["code"] == 403:
                    self.message["text"] = (
                        "Log ind på Aula for at læse denne besked."
                    )
                    self.message["sender"] = "Ukendt afsender"
                    self.message["subject"] = "Følsom besked"
                else:
                    for message in threaddata["data"]["messages"]:
                        if message["messageType"] == "Message":
                            try:
                                self.message["text"] = message["text"]["html"]
                            except (KeyError, TypeError):
                                try:
                                    self.message["text"] = message["text"]
                                except (KeyError, TypeError):
                                    self.message["text"] = "intet indhold..."
                                    _LOGGER.warning(
                                        "There is an unread message, but we cannot get the text."
                                    )
                            try:
                                self.message["sender"] = message["sender"].get(
                                    "shortName",
                                    message["sender"].get(
                                        "fullName", "Ukendt afsender"
                                    ),
                                )
                            except (KeyError, TypeError):
                                self.message["sender"] = "Ukendt afsender"
                            try:
                                self.message["subject"] = threaddata["data"]["subject"]
                            except (KeyError, TypeError):
                                self.message["subject"] = ""
                            self.unread_messages = 1
                            break
        except Exception as e:
            _LOGGER.error(f"OLD MESSAGES: Failed to fetch unread message details: {e}")
            self.unread_messages = 0

        _LOGGER.debug("OLD MESSAGES: Section completed, continuing to Calendar...")

        # Calendar:
        if self._schoolschedule:
            instProfileIds = ",".join(self._childids)
            csrf_token = self._session.cookies.get_dict().get("Csrfp-Token", "")
            headers = {
                "csrfp-token": csrf_token,
                "content-type": "application/json",
            }
            start = datetime.datetime.now(datetime.timezone.utc).strftime(
                "%Y-%m-%d 00:00:00.0000%z"
            )
            _end = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
                days=14
            )
            end = _end.strftime("%Y-%m-%d 00:00:00.0000%z")
            post_data = (
                '{"instProfileIds":['
                + instProfileIds
                + '],"resourceIds":[],"start":"'
                + start
                + '","end":"'
                + end
                + '"}'
            )
            _LOGGER.debug("Fetching calendars...")
            res = self._session.post(
                self._make_api_url("calendar.getEventsByProfileIdsAndResourceIds"),
                data=post_data,
                headers=headers,
                verify=True,
            )
            try:
                _path = self._data_path("skoleskema.json")
                fd = os.open(_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w") as skoleskema_json:
                    json.dump(res.text, skoleskema_json)
            except (OSError, ValueError) as e:
                _LOGGER.warning("Failed to write skoleskema.json: %s", e)
        # End of calendar

        # Bibliotek:
        # Fetch library data if any child is at a school-type institution
        has_school_children = any(
            itype != "kindergarten" for itype in self._institution_types.values()
        )
        if has_school_children:
            if len(self.widgets) == 0:
                self.get_widgets()

            _LOGGER.debug(
                f"Fetching library data (bibliotek flag: {self._bibliotek}, "
                f"widget 0019 available: {'0019' in self.widgets}, "
                f"institution types: {self._institution_types})"
            )

            try:
                token = self.get_token("0019")

                response = self._session.get(
                    CICERO_API
                    + "/portal-api/rest/aula/library/status/v3?"
                    + "institutions="
                    + "&institutions=".join(self._institutionProfiles)
                    + "&children="
                    + "&children=".join(self._childuserids)
                    + "&coverImageHeight=160&widgetVersion=1.6"
                    + "&userProfile=guardian"
                    + "&sessionUUID="
                    + widget_guardian(token),
                    headers={"Authorization": token, "accept": "application/json"},
                    verify=True,
                    timeout=30,
                )

                if response.status_code != 200:
                    _LOGGER.warning(
                        f"Library API returned status {response.status_code}"
                    )
                    self.library_available = False
                else:
                    books = response.json()
                    if not isinstance(books, dict) or not isinstance(books.get("loans"), list):
                        raise ValueError("Missing loans list")

                    self.loaned_books = {}
                    for loaned_book in books["loans"]:
                        book = {
                            "Title": loaned_book["title"],
                            "Author": loaned_book.get("author") or "",
                            "DueDate": loaned_book["dueDate"],
                            "NumberOfLoans": loaned_book["numberOfLoans"],
                            "Cover": str(loaned_book.get("coverImageUrl") or "").strip(),
                        }

                        if loaned_book["patronDisplayName"] not in self.loaned_books:
                            self.loaned_books[loaned_book["patronDisplayName"]] = []

                        self.loaned_books[loaned_book["patronDisplayName"]].append(book)

                    self.library_available = True
                    _LOGGER.debug(
                        f"Library books loaded. {len(self.loaned_books)} patron(s), "
                        f"{len(self._childnames)} child(ren) registered"
                    )
            except Exception as e:
                self.library_available = False
                _LOGGER.warning("Failed to fetch library data (%s)", type(e).__name__)

        # End of bibliotek

        # Min Uddannelse Forløb:
        if self._minUddannelseForloeb is True:
            if len(self.widgets) == 0:
                self.get_widgets()

            if "0028" not in self.widgets:
                _LOGGER.info("Education widget (0028) not found in available widgets")
            else:
                # Check if any child has an institution type where education widget is appropriate
                applicable_children = []
                for child_id, institution_type in self._institution_types.items():
                    if self.is_widget_appropriate_for_institution(
                        "0028", institution_type
                    ):
                        applicable_children.append(child_id)

                if not applicable_children:
                    _LOGGER.info(
                        "Education widget available but not appropriate for any child's institution type"
                    )
                else:
                    token = self.get_token("0028")
                    now = datetime.datetime.now() + datetime.timedelta(weeks=1)
                    thisweek = datetime.datetime.now().strftime("%G-W%V")
                    nextweek = now.strftime("%G-W%V")
                    self.forloebthisweek = self._minUddannelse.forloeb(
                        self._session,
                        token,
                        thisweek,
                        self._childuserids,
                        self._institutionProfiles,
                        "aula_guardian",
                    )
                    self.forloebnext = self._minUddannelse.forloeb(
                        self._session,
                        token,
                        nextweek,
                        self._childuserids,
                        self._institutionProfiles,
                        "aula_guardian",
                    )
        # End of Min Uddannelse Forløb

        # Min Uddannelse Opgave Liste
        if self._minUddannelseOpgaveListe is True:
            if len(self.widgets) == 0:
                self.get_widgets()

            if "0028" not in self.widgets:
                _LOGGER.info("Education widget (0028) not found in available widgets")
            else:
                # Check if any child has an institution type where education widget is appropriate
                applicable_children = []
                for child_id, institution_type in self._institution_types.items():
                    if self.is_widget_appropriate_for_institution(
                        "0028", institution_type
                    ):
                        applicable_children.append(child_id)

                if not applicable_children:
                    _LOGGER.info(
                        "Education widget available but not appropriate for any child's institution type"
                    )
                else:
                    token = self.get_token("0028")
                    week = datetime.datetime.now().strftime("%G-W%V")
                    opgaver = self._minUddannelse.opgaveListe(
                        self._session,
                        token,
                        week,
                        self._childuserids,
                        self._institutionProfiles,
                        "aula_guardian",
                    )

                    # Currently only one student supported
                    try:
                        _path = self._data_path("uddannelseopgaveliste.json")
                        fd = os.open(
                            _path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
                        )
                        with os.fdopen(fd, "w") as uddannelseopgaveliste_json:
                            json.dump(opgaver, uddannelseopgaveliste_json)
                    except (OSError, ValueError) as e:
                        _LOGGER.warning(
                            "Failed to write uddannelseopgaveliste.json: %s", e
                        )
        # End of Min Uddannelse Opgave Liste

        # Min Uddannelse Uge Note
        if self._minUddannelseUgeNote is True:
            if len(self.widgets) == 0:
                self.get_widgets()
            if "0028" not in self.widgets:
                _LOGGER.info("Education widget (0028) not found in available widgets")
            else:
                # Check if any child has an institution type where education widget is appropriate
                applicable_children = []
                for child_id, institution_type in self._institution_types.items():
                    if self.is_widget_appropriate_for_institution(
                        "0028", institution_type
                    ):
                        applicable_children.append(child_id)

                if not applicable_children:
                    _LOGGER.info(
                        "Education widget available but not appropriate for any child's institution type"
                    )
                else:
                    token = self.get_token("0028")
                    now = datetime.datetime.now() + datetime.timedelta(weeks=1)
                    thisweek = datetime.datetime.now().strftime("%G-W%V")
                    nextweek = now.strftime("%G-W%V")

                    try:
                        self.ugenotethisweek = self._minUddannelse.ugeBrev(
                            self._session,
                            token,
                            thisweek,
                            self._childuserids,
                            self._institutionProfiles,
                            "aula_guardian",
                        )
                    except Exception:
                        self.ugenotethisweek = {}

                    try:
                        self.ugenotenextweek = self._minUddannelse.ugeBrev(
                            self._session,
                            token,
                            nextweek,
                            self._childuserids,
                            self._institutionProfiles,
                            "aula_guardian",
                        )
                    except Exception:
                        self.ugenotenextweek = {}

        # End of Min Uddannelse Uge Note

        # Ugeplaner:
        if self._ugeplan is True:
            guardian = _safe_json(
                self._session.get(
                    self._make_api_url(
                        "profiles.getProfileContext", portalrole="guardian"
                    ),
                    verify=True,
                ),
                "profiles.getProfileContext",
            )["data"]["userId"]
            childUserIds = ",".join(self._childuserids)

            if len(self.widgets) == 0:
                self.get_widgets()

            # Check for widget availability with kindergarten awareness
            has_ugeplan_widgets = (
                "0029" in self.widgets
                or "0004" in self.widgets
                or "0062" in self.widgets
            )

            if not has_ugeplan_widgets:
                kindergarten_count = sum(
                    1
                    for child_id in self._institution_types
                    if self._institution_types[child_id] == "kindergarten"
                )
                if kindergarten_count > 0:
                    _LOGGER.info(
                        f"Week plan widgets (0029,0004,0062) not found - detected {kindergarten_count} kindergarten child(ren). Week plans may use different widgets or be unavailable for kindergartens."
                    )
                else:
                    _LOGGER.error(
                        "You have enabled ugeplaner, but we cannot find any matching widgets (0029,0004,0062) in Aula."
                    )

            if "0029" in self.widgets and "0004" in self.widgets:
                _LOGGER.warning(
                    "Multiple sources for ugeplaner is untested and might cause problems."
                )

            def ugeplan(week, thisnext):
                if "0029" in self.widgets:
                    token = self.get_token("0029")
                    get_payload = (
                        "/ugebrev?assuranceLevel=2&childFilter="
                        + childUserIds
                        + "&currentWeekNumber="
                        + week
                        + "&isMobileApp=false&placement=narrow&sessionUUID="
                        + guardian
                        + "&userProfile=guardian"
                    )
                    ugeplaner = requests.get(
                        MIN_UDDANNELSE_API + get_payload,
                        headers={"Authorization": token, "accept": "application/json"},
                        verify=True,
                    )
                    ugedata = _safe_json(ugeplaner, "MinUddannelse ugebrev")
                    for person in ugedata["personer"]:
                        ugeplan = person["institutioner"][0]["ugebreve"][0]["indhold"]
                        if thisnext == "this":
                            self.ugep_attr[person["navn"].split()[0]] = ugeplan
                        elif thisnext == "next":
                            self.ugepnext_attr[person["navn"].split()[0]] = ugeplan

                if "0062" in self.widgets:
                    _LOGGER.debug("In the Huskelisten flow...")
                    token = self.get_token("0062", False)
                    huskelisten_headers = {
                        "Accept": "application/json, text/plain, */*",
                        "Accept-Encoding": "gzip, deflate, br",
                        "Accept-Language": "en-US,en;q=0.9,da;q=0.8",
                        "Aula-Authorization": token,
                        "Origin": "https://www.aula.dk",
                        "Referer": "https://www.aula.dk/",
                        "Sec-Fetch-Dest": "empty",
                        "Sec-Fetch-Mode": "cors",
                        "Sec-Fetch-Site": "cross-site",
                        "User-Agent": "Mozilla/5.0 (X11; CrOS x86_64 15183.51.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36",
                        "zone": "Europe/Copenhagen",
                    }

                    children = "&children=".join(self._childuserids)
                    institutions = "&institutions=".join(self._institutionProfiles)
                    timedelta = datetime.datetime.now() + datetime.timedelta(days=180)
                    From = datetime.datetime.now().strftime("%Y-%m-%d")
                    dueNoLaterThan = timedelta.strftime("%Y-%m-%d")
                    get_payload = (
                        "/reminders/v1?children="
                        + children
                        + "&from="
                        + From
                        + "&dueNoLaterThan="
                        + dueNoLaterThan
                        + "&widgetVersion=1.10&userProfile=guardian&sessionId="
                        + "aula_guardian"
                        + "&institutions="
                        + institutions
                    )
                    _LOGGER.debug(
                        "Huskelisten get_payload: " + SYSTEMATIC_API + get_payload
                    )
                    #
                    mock_huskelisten = 0
                    #
                    if mock_huskelisten == 1:
                        _LOGGER.warning("Using mock data for Huskelisten.")
                        mock_huskelisten = '[{"userName":"Test Student 1","userId":100001,"courseReminders":[],"assignmentReminders":[],"teamReminders":[{"id":70001,"institutionName":"Test School","institutionId":100,"dueDate":"2022-11-29T23:00:00Z","teamId":60001,"teamName":"2A","reminderText":"Matematik lektier: Løs opgaver.","createdBy":"Teacher 1","lastEditBy":"Teacher 1","subjectName":"Matematik"},{"id":70002,"institutionName":"Test School","institutionId":100,"dueDate":"2022-12-06T23:00:00Z","teamId":60001,"teamName":"2A","reminderText":"Matematik opgave: Løs dagens opgave.","createdBy":"Teacher 1","lastEditBy":"Teacher 2","subjectName":"Matematik"},{"id":70003,"institutionName":"Test School","institutionId":100,"dueDate":"2022-12-13T23:00:00Z","teamId":60001,"teamName":"2A","reminderText":"Matematik opgave: Løs dagens opgave.","createdBy":"Teacher 1","lastEditBy":"Teacher 1","subjectName":"Matematik"},{"id":70004,"institutionName":"Test School","institutionId":100,"dueDate":"2022-12-20T23:00:00Z","teamId":60001,"teamName":"2A","reminderText":"Matematik opgave: Løs dagens opgave.","createdBy":"Teacher 2","lastEditBy":"Teacher 2","subjectName":"Matematik"}]},{"userName":"Test Student 2","userId":100002,"courseReminders":[],"assignmentReminders":[{"id":0,"institutionName":"Test School","institutionId":100,"dueDate":"2022-12-08T11:00:00Z","courseId":200001,"teamNames":["5A","5B"],"teamIds":[60002,60003],"courseSubjects":[],"assignmentId":500001,"assignmentText":"Skriv en opgave"}],"teamReminders":[{"id":70005,"institutionName":"Test School","institutionId":100,"dueDate":"2022-11-30T23:00:00Z","teamId":60003,"teamName":"5A","reminderText":"Læse opgave fra bog.","createdBy":"Teacher 3","lastEditBy":"Teacher 3","subjectName":"Dansk"}]},{"userName":"Test Student 3","userId":100003,"courseReminders":[],"assignmentReminders":[],"teamReminders":[]}]'
                        data = json.loads(mock_huskelisten, strict=False)
                    else:
                        response = requests.get(
                            SYSTEMATIC_API + get_payload,
                            headers=huskelisten_headers,
                            verify=True,
                        )
                        try:
                            data = json.loads(response.text, strict=False)
                        except (json.JSONDecodeError, ValueError) as e:
                            _LOGGER.error(
                                "Could not parse the response from Huskelisten as json: %s",
                                e,
                            )

                    for person in data:
                        name = person["userName"].split()[0]
                        _LOGGER.debug(
                            "Huskelisten for child ID %s",
                            person.get("userId", "unknown"),
                        )
                        huskel = ""
                        reminders = person["teamReminders"]
                        if len(reminders) > 0:
                            for reminder in reminders:
                                mytime = datetime.datetime.strptime(
                                    reminder["dueDate"], "%Y-%m-%dT%H:%M:%SZ"
                                )
                                ftime = mytime.strftime("%A %d. %B")
                                huskel = huskel + "<h3>" + ftime + "</h3>"
                                huskel = (
                                    huskel
                                    + "<b>"
                                    + reminder["subjectName"]
                                    + "</b><br>"
                                )
                                huskel = (
                                    huskel + "af " + reminder["createdBy"] + "<br><br>"
                                )
                                content = re.sub(
                                    r"([0-9]+)(\.)", r"\1\.", reminder["reminderText"]
                                )
                                huskel = huskel + content + "<br><br>"
                        else:
                            huskel = huskel + str(name) + " har ingen påmindelser."
                        self.huskeliste[name] = huskel

                # End Huskelisten
                if "0004" in self.widgets:
                    # Try Meebook:
                    _LOGGER.debug("In the Meebook flow...")
                    token = self.get_token("0004")
                    headers = {
                        "authority": "app.meebook.com",
                        "accept": "application/json",
                        "authorization": token,
                        "dnt": "1",
                        "origin": "https://www.aula.dk",
                        "referer": "https://www.aula.dk/",
                        "sessionuuid": "aula_session",
                        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Safari/537.36",
                        "x-version": "1.0",
                    }
                    childFilter = "&childFilter[]=".join(self._childuserids)
                    institutionFilter = "&institutionFilter[]=".join(
                        self._institutionProfiles
                    )
                    get_payload = (
                        "/relatedweekplan/all?currentWeekNumber="
                        + week
                        + "&userProfile=guardian&childFilter[]="
                        + childFilter
                        + "&institutionFilter[]="
                        + institutionFilter
                    )

                    mock_meebook = 0
                    if mock_meebook == 1:
                        _LOGGER.warning("Using mock data for Meebook ugeplaner.")
                        mock_meebook = '[{"id":490000,"name":"Test Barn 1","unilogin":"test001","weekPlan":[{"date":"mandag 28. nov.","tasks":[{"id":3069630,"type":"comment","author":"Lærer A","group":"3.a - ugeplan","pill":"Ingen fag tilknyttet","content":"Eksempel indhold til test.","editUrl":"https://example.com/test/202248"}]},{"date":"tirsdag 29. nov.","tasks":[{"id":3069631,"type":"comment","author":"Lærer A","group":"3.a - ugeplan","pill":"Ingen fag tilknyttet","content":"Eksempel indhold til test.","editUrl":"https://example.com/test/202248"}]}]},{"id":630000,"name":"Test Barn 2","unilogin":"test002","weekPlan":[{"date":"mandag 28. nov.","tasks":[{"id":3090189,"type":"comment","author":"Lærer B","group":"0C (22/23)","pill":"Dansk, Matematik","content":"Eksempel indhold til test.","editUrl":"https://example.com/test/202248"}]}]}]'
                        data = json.loads(mock_meebook, strict=False)
                    else:
                        response = requests.get(
                            MEEBOOK_API + get_payload, headers=headers, verify=True
                        )
                        data = json.loads(response.text, strict=False)

                    for person in data:
                        _LOGGER.debug(
                            "Meebook ugeplan for child ID %s",
                            person.get("id", "unknown"),
                        )
                        ugep = ""
                        ugeplan = person["weekPlan"]
                        for day in ugeplan:
                            ugep = ugep + "<h3>" + day["date"] + "</h3>"
                            if len(day["tasks"]) > 0:
                                for task in day["tasks"]:
                                    if not task["pill"] == "Ingen fag tilknyttet":
                                        ugep = ugep + "<b>" + task["pill"] + "</b><br>"
                                    ugep = ugep + task["author"] + "<br><br>"
                                    content = re.sub(
                                        r"([0-9]+)(\.)", r"\1\.", task["content"]
                                    )
                                    ugep = ugep + content + "<br><br>"
                            else:
                                ugep = ugep + "-"
                        try:
                            name = person["name"].split()[0]
                        except (KeyError, IndexError, AttributeError):
                            name = person["name"]
                        if thisnext == "this":
                            self.ugep_attr[name] = ugep
                        elif thisnext == "next":
                            self.ugepnext_attr[name] = ugep

            now = datetime.datetime.now() + datetime.timedelta(weeks=1)
            thisweek = datetime.datetime.now().strftime("%G-W%V")
            nextweek = now.strftime("%G-W%V")
            ugeplan(thisweek, "this")
            ugeplan(nextweek, "next")
        # End of Ugeplaner

        # Posts (Indlæg):
        try:
            self._get_posts()
            _LOGGER.debug("Posts data retrieved successfully")
        except Exception as e:
            _LOGGER.error(f"Failed to retrieve posts: {e}", exc_info=True)
            # Initialize empty posts data on error
            self.posts = {}
            self.posts_by_child = {}

        # Mail Threads:
        try:
            self._get_mail()
            mail_count = len(getattr(self, "mail_threads", {}))
            child_count = len(getattr(self, "mail_by_child", {}))
            _LOGGER.debug(
                f"Retrieved {mail_count} mail threads for {child_count} children"
            )
        except Exception as e:
            _LOGGER.error(f"Failed to retrieve mail: {e}", exc_info=True)
            # Initialize empty mail data on error
            self.mail_threads = {}
            self.mail_by_child = {}
