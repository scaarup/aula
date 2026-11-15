"""Library books sensor for Aula integration."""

import logging

from homeassistant.helpers.entity import Entity

from ..const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class AulaLibrarySensor(Entity):
    """Library books sensor for a child."""

    def __init__(self, hass, coordinator, child) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._child = child
        self._client = hass.data[DOMAIN]["client"]

    def _find_books_for_child(self):
        """Find loaned books for this child, handling name mismatches between Aula and CICERO.

        The CICERO API keys books by patronDisplayName which may differ from the
        Aula child name (e.g. first name only vs full name). This method tries:
        1. Exact match on child["name"]
        2. Match on the childnames lookup (full name from profile)
        3. Partial match: patronDisplayName contains or is contained in child name
        """
        loaned = self._client.loaned_books
        if not loaned:
            return None

        child_name = self._child.get("name", "")
        # Also get the name from _childnames which may be formatted differently
        profile_name = self._client._childnames.get(self._child["id"], "")

        # 1. Exact match on child["name"]
        if child_name in loaned:
            return loaned[child_name]

        # 2. Exact match on profile name
        if profile_name and profile_name in loaned:
            return loaned[profile_name]

        # 3. Partial/first-name matching
        child_first = child_name.split()[0].lower() if child_name else ""
        profile_first = profile_name.split()[0].lower() if profile_name else ""

        for patron_name, books in loaned.items():
            patron_lower = patron_name.lower()
            patron_first = patron_lower.split()[0] if patron_lower else ""

            # First name match
            if child_first and (
                patron_first == child_first or patron_lower == child_first
            ):
                return books
            if profile_first and (
                patron_first == profile_first or patron_lower == profile_first
            ):
                return books

            # Substring match (patron in child name or vice versa)
            if child_name and (
                patron_lower in child_name.lower() or child_name.lower() in patron_lower
            ):
                return books

        return None

    @property
    def name(self):
        childname = self._client._childnames[self._child["id"]].split()[0]
        return f"Library - {childname}"

    @property
    def state(self):
        books = self._find_books_for_child()
        if books is not None and isinstance(books, list):
            return len(books)
        return 0

    @property
    def extra_state_attributes(self):
        attributes = {}
        books = self._find_books_for_child() if self._client.library_available else None
        if books is not None:
            attributes["books"] = books
        else:
            attributes["books"] = []
        return attributes

    @property
    def unique_id(self):
        childname = (
            self._client._childnames[self._child["id"]]
            .split()[0]
            .lower()
            .replace(" ", "_")
        )
        return f"library_{childname}"

    @property
    def icon(self):
        return "mdi:book-multiple"

    @property
    def unit_of_measurement(self):
        return "books"

    @property
    def should_poll(self):
        return False

    @property
    def available(self):
        return self._coordinator.last_update_success and self._client.library_available

    async def async_update(self):
        await self._coordinator.async_request_refresh()

    async def async_added_to_hass(self):
        self.async_on_remove(
            self._coordinator.async_add_listener(self.async_write_ha_state)
        )
