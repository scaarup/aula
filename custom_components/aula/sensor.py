"""
Based on https://github.com/JBoye/HA-Aula
"""
from .const import DOMAIN
import logging
from datetime import datetime, date, timedelta
import voluptuous as vol
from typing import Final
from homeassistant.util import dt as dt_util
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant import config_entries, core
from .client import Client
from . import helpers

_LOGGER = logging.getLogger(__name__)

from homeassistant.const import (
    CONF_USERNAME,
    CONF_PASSWORD
)
from .const import (
    CONF_SCHOOLSCHEDULE,
    CONF_UGEPLAN,
    DOMAIN
)

EVENT_START_DATE = "start_date"
EVENT_END_DATE = "end_date"
EVENT_DURATION = "duration"

LIST_MEEBOOK_EVENTS_SERVICE_NAME = "list_meebook_events"
LIST_MEEBOOK_EVENTS_SCHEMA: Final = vol.All(
    cv.has_at_least_one_key(EVENT_END_DATE, EVENT_DURATION),
    cv.has_at_most_one_key(EVENT_END_DATE, EVENT_DURATION),
    cv.make_entity_service_schema(
        {
            vol.Optional(EVENT_START_DATE): cv.date,
            vol.Optional(EVENT_END_DATE): cv.date,
            vol.Optional(EVENT_DURATION): vol.All(
                cv.time_period, cv.positive_timedelta
            ),
        }
    ),
)

PARALLEL_UPDATES = 1

async def async_setup_entry(
    hass: core.HomeAssistant,
    config_entry: config_entries.ConfigEntry,
    async_add_entities,
):
    """Setup sensors from a config entry created in the integrations UI."""
    config = hass.data[DOMAIN][config_entry.entry_id]

    if config_entry.options:
        config.update(config_entry.options)
    #from .client import Client
    client  = Client(config[CONF_USERNAME], config[CONF_PASSWORD],config[CONF_SCHOOLSCHEDULE],config[CONF_UGEPLAN])
    hass.data[DOMAIN]["client"] = client


    async def async_update_data():
        client = hass.data[DOMAIN]["client"]
        await hass.async_add_executor_job(client.update_data)

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="sensor",
        update_method=async_update_data,
        update_interval=timedelta(minutes=5)
    )

    # Immediate refresh
    await coordinator.async_request_refresh()

    entities = []
    client = hass.data[DOMAIN]["client"]
    await hass.async_add_executor_job(client.update_data)
    for i, child in enumerate(client._children):
        #_LOGGER.debug("Presence data for child "+str(child["id"])+" : "+str(client.presence[str(child["id"])]))
        if client.presence[str(child["id"])] == 1:
            if str(child["id"]) in client._daily_overview:
                _LOGGER.debug("Found presence data for childid "+str(child["id"])+" adding sensor entity.")
                entities.append(AulaSensor(hass, coordinator, child))
        else:
            entities.append(AulaSensor(hass, coordinator, child))
    # We have data and can now set up the calendar platform:
    if config[CONF_SCHOOLSCHEDULE]:
        hass.async_create_task(
            hass.config_entries.async_forward_entry_setup(config_entry, "calendar")
        )
####
    hass.async_create_task(
        hass.config_entries.async_forward_entry_setup(config_entry, "binary_sensor")
    )
####
    #
    global ugeplan
    if config[CONF_UGEPLAN]:
        ugeplan = True
    else:
        ugeplan = False
    async_add_entities(entities,update_before_add=True)


    # Set up services
    if len(client.meebook_weekplan) > 0:
        platform = entity_platform.async_get_current_platform()
        async def meebook_list_events_service(service_call: core.ServiceCall) -> core.ServiceResponse:
            """Search in the date range and return the matching items."""
            start = service_call.data.get(EVENT_START_DATE, dt_util.now().date())
            if EVENT_DURATION in service_call.data:
                end = start + service_call.data[EVENT_DURATION]
            else:
                end = service_call.data[EVENT_END_DATE]

            sensors = await platform.async_extract_from_service(service_call)
            sensor = sensors[0]

            return await sensor.async_get_meebook_weekplan(start, end)

        hass.services.async_register(
            DOMAIN,
            LIST_MEEBOOK_EVENTS_SERVICE_NAME,
            meebook_list_events_service,
            schema=LIST_MEEBOOK_EVENTS_SCHEMA,
            supports_response=core.SupportsResponse.ONLY,
        )



class AulaSensor(Entity):
    def __init__(self, hass, coordinator, child) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._child = child
        self._client = hass.data[DOMAIN]["client"]

    @property
    def name(self):
        childname = self._client._childnames[self._child["id"]].split()[0]
        institution = self._client._institutions[self._child["id"]]
        return institution + " " + childname

    @property
    def state(self):
        """
            0 = IKKE KOMMET
            1 = SYG
            2 = FERIE/FRI
            3 = KOMMET/TIL STEDE
            4 = PÅ TUR
            5 = SOVER
            8 = HENTET/GÅET
        """
        if self._client.presence[str(self._child["id"])] == 1:
            states = ["Ikke kommet", "Syg", "Ferie/Fri", "Kommet/Til stede", "På tur", "Sover", "6", "7", "Gået", "9", "10", "11", "12", "13", "14", "15"]
            daily_info = self._client._daily_overview[str(self._child["id"])]
            return states[daily_info["status"]]
        else:
            _LOGGER.debug("Setting state to n/a for child "+str(self._child["id"]))
            return "n/a"

    @property
    def extra_state_attributes(self):
        if self._client.presence[str(self._child["id"])] == 1:
            daily_info = self._client._daily_overview[str(self._child["id"])]
            try:
                profilePicture = daily_info["institutionProfile"]["profilePicture"]["url"]
            except:
                profilePicture = None

        fields = ['location', 'sleepIntervals', 'checkInTime', 'checkOutTime', 'activityType', 'entryTime', 'exitTime', 'exitWith', 'comment', 'spareTimeActivity', 'selfDeciderStartTime', 'selfDeciderEndTime']
        attributes = {}
        #_LOGGER.debug("Dump of weekplans_html: "+str(self._client.weekplans_html))
        if ugeplan:
            if "0062" in self._client.widgets:
                try:
                    attributes["huskelisten"] = self._client.huskeliste[self._child["name"].split()[0]]
                except:
                    attributes["huskelisten"] = "Not available"
            try:
                attributes["ugeplan"] = self._client.weekplans_html[self._child["name"].split()[0]][helpers.get_this_week_start_date()]
            except:
                attributes["ugeplan"] = "Not available"
            try:
                attributes["ugeplan"] = self._client.weekplans_html[self._child["name"].split()[0]][helpers.get_next_week_start_date()]
            except:
                attributes["ugeplan_next"] = "Not available"
                _LOGGER.debug("Could not get ugeplan for next week for child "+str(self._child["name"].split()[0])+". Perhaps not available yet.")
        if self._client.presence[str(self._child["id"])] == 1:
            for attribute in fields:
                if attribute == "exitTime" and daily_info[attribute] == "23:59:00":
                    attributes[attribute] = None
                else:
                    try:
                        attributes[attribute] = datetime.strptime(daily_info[attribute], "%H:%M:%S").strftime("%H:%M")
                    except:
                        attributes[attribute] = daily_info[attribute]
            attributes["profilePicture"] = profilePicture
        return attributes

    @property
    def should_poll(self):
        """No need to poll. Coordinator notifies entity of updates."""
        return False

    @property
    def available(self):
        """Return if entity is available."""
        return self._coordinator.last_update_success

    @property
    def unique_id(self):
        unique_id = "aula"+str(self._child["id"])
        _LOGGER.debug("Unique ID for child "+str(self._child["id"])+" "+unique_id)
        return unique_id

    @property
    def icon(self):
        return 'mdi:account-school'

    async def async_get_meebook_weekplan (
        self, start_date: date, end_date: date
    ):
        try:
            name = self._child["name"].split()[0]
            plan = self._client.meebook_weekplan[name]
            result = {"entries": {day: tasks for day, tasks in plan.items() if day >= start_date and day <= end_date and len(tasks["tasks"]) > 0 }}

            warnings = []
            if len(plan) == 0:
                warnings.append("No plan found")
            else:
                # check if data requested outside bounds of existing data
                date_lower = min(plan.keys())
                date_upper = max(plan.keys())
                if start_date < date_lower or end_date > date_upper:
                    warnings.append(f"Start or end date is outside available data range: {date_lower} - {date_upper}")

            if len(warnings) > 0:
                result["warnings"] = warnings

            return result
        except:
            return {}

    async def async_update(self):
        """Update the entity. Only used by the generic entity update service."""
        await self._coordinator.async_request_refresh()

    async def async_added_to_hass(self):
        """When entity is added to hass."""
        self.async_on_remove(
            self._coordinator.async_add_listener(
                self.async_write_ha_state
            )
        )
