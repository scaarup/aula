from datetime import datetime, timedelta
import logging, time
from .const import DOMAIN
from homeassistant import config_entries, core
from .const import CONF_SCHOOLSCHEDULE
from homeassistant.components.calendar import (
    CalendarEntity,
    CalendarEvent,
)
from homeassistant.util import Throttle

_LOGGER = logging.getLogger(__name__)

MIN_TIME_BETWEEN_UPDATES = timedelta(minutes=10)
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: core.HomeAssistant,
    config_entry: config_entries.ConfigEntry,
    async_add_entities,
):
    config = hass.data[DOMAIN][config_entry.entry_id]
    if config_entry.options:
        config.update(config_entry.options)
    from .client import Client

    if not config[CONF_SCHOOLSCHEDULE] == True:
        return True
    client = hass.data[DOMAIN]["client"]
    calendar_devices = []
    calendar = []
    for i, child in enumerate(client._children):
        childid = child["id"]
        name = child["name"]
        calendar_devices.append(CalendarDevice(hass, calendar, name, childid))
    async_add_entities(calendar_devices)


class CalendarDevice(CalendarEntity):
    def __init__(self, hass, calendar, name, childid):
        self.data = CalendarData(hass, calendar, childid)
        self._cal_data = {}
        self._name = "Skoleskema " + name
        self._childid = childid

    @property
    def event(self):
        """Return the next upcoming event."""
        return self.data.event

    @property
    def name(self):
        """Return the name of the entity."""
        return self._name

    @property
    def unique_id(self):
        unique_id = "aulacalendar" + str(self._childid)
        _LOGGER.debug("Unique ID for calendar " + str(self._childid) + " " + unique_id)
        return unique_id

    def update(self):
        """Update all Calendars."""
        self.data.update()

    async def async_get_events(self, hass, start_date, end_date):
        """Get all events in a specific time frame."""
        return await self.data.async_get_events(hass, start_date, end_date)


class CalendarData:
    def __init__(self, hass, calendar, childid):
        self.event = None

        self._hass = hass
        self._calendar = calendar
        self._childid = childid

        self.all_events = []
        self._client = hass.data[DOMAIN]["client"]

    def parseCalendarData(self, i=None):
        import json

        try:
            with open("skoleskema.json", "r") as openfile:
                _data = json.load(openfile)
            data = json.loads(_data)
        except:
            _LOGGER.warn("Could not open and parse file skoleskema.json!")
            return False
        events = []
        _LOGGER.debug("Parsing skoleskema.json...")
        for c in data["data"]:
            if c["type"] == "lesson" and c["belongsToProfiles"][0] == self._childid:
                event = parseCalendarLesson(c)
                events.append(event)
        return events

    async def async_get_events(self, hass, start_date, end_date):
        all_events = self.parseCalendarData()
        filtered_events = []

        for event in all_events:
            if event.end > start_date and event.start < end_date:
                filtered_events.append(event)

        return filtered_events

    @Throttle(MIN_TIME_BETWEEN_UPDATES)
    def update(self):
        _LOGGER.debug("Updating calendars...")
        self.parseCalendarData(self)


def parseCalendarLesson(lesson):
    summary = lesson["title"]
    start = datetime.strptime(lesson["startDateTime"], "%Y-%m-%dT%H:%M:%S%z")
    end = datetime.strptime(lesson["endDateTime"], "%Y-%m-%dT%H:%M:%S%z")
    location = (lesson.get("primaryResource", {}) or {}).get("name")
    vikar = 0
    for p in lesson["lesson"]["participants"]:
        if p["participantRole"] == "substituteTeacher":
            teacher = "VIKAR: " + p["teacherName"]
            vikar = 1
            break
    if vikar == 0:
        try:
            teacher = lesson["lesson"]["participants"][0]["teacherInitials"]
        except:
            try:
                _LOGGER.debug("Lesson json dump" + str(lesson["lesson"]))
                teacher = lesson["lesson"]["participants"][0]["teacherName"]
            except:
                _LOGGER.debug(
                    "Could not find any teacher information for "
                    + summary
                    + " at "
                    + str(start)
                )
                teacher = ""
    lesson = CalendarEvent(
        summary=str(summary) + ", " + str(teacher),
        start=start,
        end=end,
        location=location,
    )
    return lesson
