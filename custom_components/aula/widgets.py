"""
Widget management for Aula integration.
Handles widget detection, token management, and institution type detection.
"""

import logging
import re
import time

from homeassistant.exceptions import ConfigEntryNotReady

from .minuddannelse import MinUddannelse

_LOGGER = logging.getLogger(__name__)


class WidgetsMixin:
    """Mixin providing widget-related methods for the Aula Client."""

    def get_widgets(self):
        try:
            # Use profiles.getProfileContext to get widget configurations
            response = self._session.get(
                self._make_api_url("profiles.getProfileContext", portalrole="guardian"),
                headers=self._auth_headers(),
                verify=True,
            )
            if response.status_code != 200:
                _LOGGER.warning(
                    f"profiles.getProfileContext returned status {response.status_code}"
                )
                return
            profile_context = response.json()

            if profile_context.get("status", {}).get("message") == "OK":
                widget_configs = (
                    profile_context.get("data", {})
                    .get("pageConfiguration", {})
                    .get("widgetConfigurations", [])
                )

                # Extract widget IDs and names from the configuration
                for widget_config in widget_configs:
                    widget = widget_config.get("widget", {})
                    widget_id = widget.get("widgetId", "")
                    widget_name = widget.get("name", "")
                    if widget_id and widget_name:
                        self.widgets[widget_id] = widget_name

                _LOGGER.debug(
                    "Widgets found from profile context: " + str(self.widgets)
                )

                # Dynamically set widget flags based on detected widgets and institution appropriateness
                self._bibliotek = self._should_enable_widget("0019")  # Library
                self._minUddannelseForloeb = self._should_enable_widget(
                    "0028"
                )  # Education/Learning
                self._minUddannelseOpgaveListe = self._should_enable_widget(
                    "0028"
                )  # Same widget for different features
                self._minUddannelseUgeNote = self._should_enable_widget(
                    "0028"
                )  # Same widget for different features

                # Update MinUddannelse instance with dynamic settings
                if hasattr(self, "_minUddannelse"):
                    self._minUddannelse._minUddannelseForloeb = (
                        self._minUddannelseForloeb
                    )
                    self._minUddannelse._minUddannelseOpgaveListe = (
                        self._minUddannelseOpgaveListe
                    )
                    self._minUddannelse._minUddannelseugenote = (
                        self._minUddannelseUgeNote
                    )
                else:
                    # Create MinUddannelse if not already exists
                    self._minUddannelse = MinUddannelse(
                        self._minUddannelseForloeb,
                        self._minUddannelseOpgaveListe,
                        self._minUddannelseUgeNote,
                    )

                _LOGGER.info(
                    f"Dynamic widget flags set: bibliotek={self._bibliotek}, minUddannelse_forloeb={self._minUddannelseForloeb}, minUddannelse_opgaveliste={self._minUddannelseOpgaveListe}, minUddannelse_ugenote={self._minUddannelseUgeNote}"
                )

            else:
                _LOGGER.warning("Failed to get profile context for widgets")
        except Exception as e:
            _LOGGER.error(f"Error getting widgets from profile context: {type(e).__name__}")

    def _should_enable_widget(self, widget_id):
        """Check if a widget should be enabled based on availability and institution appropriateness"""
        if widget_id not in self.widgets:
            return False

        # Check if any child has an institution type where this widget is appropriate
        for child_id, institution_type in self._institution_types.items():
            if self.is_widget_appropriate_for_institution(widget_id, institution_type):
                return True

        return False

    def is_widget_appropriate_for_institution(self, widget_id, institution_type):
        """Check if a widget is appropriate for a specific institution type"""
        # Widgets that are only appropriate for schools (not kindergartens)
        school_only_widgets = {
            "0019": "Library",  # Libraries typically not available in kindergartens
            "0028": "Education/Learning",  # Advanced learning features for schools
            "0062": "Reminders",  # Reminder system more relevant for older students
        }

        # If it's a kindergarten and the widget is school-only, return False
        if institution_type == "kindergarten" and widget_id in school_only_widgets:
            _LOGGER.debug(
                f"Widget {widget_id} ({school_only_widgets[widget_id]}) filtered out for kindergarten"
            )
            return False

        # All other widgets (attendance, schedules, etc.) are appropriate for all types
        return True

    def _detect_institution_type(self, institution_profile):
        """Detect institution type from profile data"""
        # First try to get institution type from the institution object
        if "institution" in institution_profile and institution_profile[
            "institution"
        ].get("type"):
            institution_type = institution_profile["institution"]["type"].lower()
            # Map API types to our standardized names
            if institution_type == "daycare":
                return "kindergarten"
            elif institution_type == "school":
                return "school"
            else:
                return institution_type

        # Fallback check for institutionType field
        if institution_profile.get("institutionType"):
            institution_type = institution_profile["institutionType"].lower()
            if institution_type == "daycare":
                return "kindergarten"
            elif institution_type == "school":
                return "school"
            else:
                return institution_type

        # Fallback detection based on name and metadata
        institution_name = institution_profile.get("institutionName", "").lower()
        metadata = institution_profile.get("metadata", "").lower()

        # Common kindergarten indicators
        kindergarten_keywords = [
            "børnehave",
            "vuggestue",
            "dagpleje",
            "kindergarten",
            "pionererne",
            "gardikjærgård",
            "gadkjærgård",
            "børnehus",
            "dagplejen",
        ]

        # Common school indicators
        school_keywords = [
            "skole",
            "school",
            "gymnasium",
            "erhvervsskole",
            "teknisk skole",
            "handelsskole",
            "hf",
            "ht",
        ]

        for keyword in kindergarten_keywords:
            if keyword in institution_name or keyword in metadata:
                return "kindergarten"

        for keyword in school_keywords:
            if keyword in institution_name or keyword in metadata:
                return "school"

        # Check if metadata contains class indicators (like "2.3" for 2nd grade class 3)
        if re.match(r"^\d+\.\d+$", metadata.strip()):
            return "school"

        # Default fallback based on institution name patterns
        if "skole" in institution_name:
            return "school"
        elif any(kw in institution_name for kw in ["gård", "hus", "have"]):
            return "kindergarten"

        return "unknown"

    def get_token(self, widgetid, mock=False):
        """Get Bearer token for specific widget API calls"""
        _LOGGER.debug("Requesting token for widget " + widgetid)
        if mock:
            return "MockToken"

        # Check if we already have a token for this widget
        cached = self.tokens.get(widgetid)
        if isinstance(cached, tuple) and time.monotonic() - cached[1] < 60:
            return cached[0]

        try:
            # Get token using current session
            response = self._session.get(
                self._make_api_url("aulaToken.getAulaToken", widgetId=widgetid),
                headers=self._auth_headers(),
                verify=True,
                timeout=10,
            )

            if response.status_code != 200:
                _LOGGER.error(
                    f"Token request failed with status {response.status_code} for widget {widgetid}"
                )
                raise Exception(f"Token request failed: {response.status_code}")

            response_data = response.json()
            if "data" not in response_data:
                _LOGGER.error(
                    f"Token response missing data for widget {widgetid}"
                )
                raise Exception("Token response missing data")

            token = "Bearer " + str(response_data["data"])
            self.tokens[widgetid] = (token, time.monotonic())

            _LOGGER.debug(f"Successfully obtained token for widget {widgetid}")
            return token

        except Exception as e:
            _LOGGER.error(f"Failed to get token for widget {widgetid}: {type(e).__name__}")
            raise ConfigEntryNotReady(
                f"Failed to obtain API token for widget {widgetid}: {type(e).__name__}"
            )
