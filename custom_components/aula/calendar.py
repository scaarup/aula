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
    for i in range(0,100):
        try:
            a = client._children
        except:
            _LOGGER.debug(str(i)+" no client._children ... yet")
        else:
            _LOGGER.debug("Got client._children, breaking")
            break
        time.sleep(0.400)
    for i, child in enumerate(client._children):
        childid = child["id"]
        name = child["name"]
        calendar_devices.append(CalendarDevice(hass,calendar,name,childid))
    async_add_entities(calendar_devices)

class CalendarDevice(CalendarEntity):
    def __init__(self,hass,calendar,name,childid):
        self.data = CalendarData(hass,calendar,childid)
        self._cal_data = {}
        self._name = "Skoleskema "+name

    @property
    def event(self):
        """Return the next upcoming event."""
        return self.data.event

    @property
    def name(self):
        """Return the name of the entity."""
        return self._name

    def update(self):
        """Update all Calendars."""
        self.data.update()
        
    async def async_get_events(self, hass, start_date, end_date):
        """Get all events in a specific time frame."""
        return await self.data.async_get_events(hass, start_date, end_date)

class CalendarData:
    def __init__(self,hass,calendar,childid):
        self.event = None

        self._hass = hass
        self._calendar = calendar
        self._childid = childid

        self.all_events = []
        self._client = hass.data[DOMAIN]["client"]

    def parseCalendarData(self,i=None):
        import json
        try:
            with open('skoleskema.json', 'r') as openfile:
                _data = json.load(openfile)
            data = json.loads(_data)
        except:
            _LOGGER.warn("Could not open and parse file skoleskema.json!")
            return False
        events = []
        _LOGGER.debug("Parsing skoleskema.json...")
        for c in data['data']:
            if c['type'] == "lesson" and c['belongsToProfiles'][0] == self._childid:
                summary = c['title']
                start = datetime.strptime(c['startDateTime'],"%Y-%m-%dT%H:%M:%S%z")
                end = datetime.strptime(c['endDateTime'],"%Y-%m-%dT%H:%M:%S%z")
                vikar = 0
                for p in c['lesson']['participants']:
                    if p['participantRole'] == 'substituteTeacher':
                        teacher = "VIKAR: "+p['teacherName']
                        vikar = 1
                        break
                if vikar == 0:
                    teacher = c['lesson']['participants'][0]['teacherInitials']
                event = CalendarEvent(
                    summary=summary+", "+teacher,
                    start = start,
                    end = end,
                )
                events.append(event)
        return events

    async def async_get_events(self, hass, start_date, end_date):
        events = self.parseCalendarData()
        return events

    @Throttle(MIN_TIME_BETWEEN_UPDATES)
    def update(self):
        _LOGGER.debug("Updating calendars...")
        self.parseCalendarData(self)