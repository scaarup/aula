"""Education and week notes sensors for Aula integration."""

import logging

from homeassistant.helpers.entity import Entity

from ..const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class AulaEducationSensor(Entity):
    """Education courses sensor for a child."""

    def __init__(self, hass, coordinator, child) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._child = child
        self._client = hass.data[DOMAIN]["client"]

    @property
    def name(self):
        childname = self._client._childnames[self._child["id"]].split()[0]
        return f"Education - {childname}"

    @property
    def state(self):
        try:
            forloeb = self._client.forloebthisweek[self._child["name"]]
            if forloeb and forloeb != "Not available":
                return "active"
            return "none"
        except (KeyError, TypeError):
            return "unavailable"

    @property
    def extra_state_attributes(self):
        attributes = {}
        try:
            attributes["course_this_week"] = self._client.forloebthisweek[
                self._child["name"]
            ]
        except (KeyError, TypeError):
            attributes["course_this_week"] = "Not available"

        try:
            attributes["course_next_week"] = self._client.forloebnext[
                self._child["name"]
            ]
        except (KeyError, TypeError):
            attributes["course_next_week"] = "Not available"

        return attributes

    @property
    def unique_id(self):
        childname = (
            self._client._childnames[self._child["id"]]
            .split()[0]
            .lower()
            .replace(" ", "_")
        )
        return f"education_{childname}"

    @property
    def icon(self):
        # Dynamic icons based on institution type
        child_id = self._child["id"]
        institution_type = getattr(self._client, "_institution_types", {}).get(
            child_id, "unknown"
        )

        if institution_type == "kindergarten":
            return "mdi:human-child"
        elif institution_type == "school":
            return "mdi:school"
        else:
            return "mdi:book-education"

    @property
    def should_poll(self):
        return False

    @property
    def available(self):
        return self._coordinator.last_update_success

    async def async_update(self):
        await self._coordinator.async_request_refresh()

    async def async_added_to_hass(self):
        self.async_on_remove(
            self._coordinator.async_add_listener(self.async_write_ha_state)
        )


class AulaWeekNotesSensor(Entity):
    """Weekly notes sensor for a child."""

    def __init__(self, hass, coordinator, child) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._child = child
        self._client = hass.data[DOMAIN]["client"]

    @property
    def name(self):
        childname = self._client._childnames[self._child["id"]].split()[0]
        return f"Week Notes - {childname}"

    @property
    def state(self):
        try:
            # ugenotethisweek and ugenotenextweek are text strings, not dictionaries
            note_this = getattr(self._client, "ugenotethisweek", None)
            note_next = getattr(self._client, "ugenotenextweek", None)

            # Check if we have valid note content
            has_notes = (
                note_this and isinstance(note_this, str) and note_this.strip()
            ) or (note_next and isinstance(note_next, str) and note_next.strip())

            return "available" if has_notes else "none"
        except Exception as e:
            _LOGGER.debug(f"Week notes sensor state error for {self._child['id']}: {e}")
            return "unavailable"

    @property
    def extra_state_attributes(self):
        attributes = {}

        try:
            # ugenotethisweek is a text string, not a dictionary
            note_this = getattr(self._client, "ugenotethisweek", None)
            if note_this and isinstance(note_this, str):
                attributes["week_note_this_week"] = note_this
            else:
                attributes["week_note_this_week"] = "Not available"
        except Exception:
            attributes["week_note_this_week"] = "Not available"

        try:
            # ugenotenextweek is a text string, not a dictionary
            note_next = getattr(self._client, "ugenotenextweek", None)
            if note_next and isinstance(note_next, str):
                attributes["week_note_next_week"] = note_next
            else:
                attributes["week_note_next_week"] = "Not available"
        except Exception:
            attributes["week_note_next_week"] = "Not available"

        return attributes

    @property
    def unique_id(self):
        childname = (
            self._client._childnames[self._child["id"]]
            .split()[0]
            .lower()
            .replace(" ", "_")
        )
        return f"week_notes_{childname}"

    @property
    def icon(self):
        return "mdi:notebook"

    @property
    def should_poll(self):
        return False

    @property
    def available(self):
        return self._coordinator.last_update_success

    async def async_update(self):
        await self._coordinator.async_request_refresh()

    async def async_added_to_hass(self):
        self.async_on_remove(
            self._coordinator.async_add_listener(self.async_write_ha_state)
        )
