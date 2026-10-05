from datetime import datetime, timedelta, date
import logging, time
from .const import (
    DOMAIN,
    CONF_SCHOOLSCHEDULE,
    CONF_SCHOOLSCHEDULE_EMOJI,
    CONF_UGEPLAN,
    CONF_MEETINGS,
    TEACHER_NAME_INITIALS,
    TEACHER_NAME_FULL,
    TEACHER_NAME_FIRST_NAME_INITIALS,
    resolve_teacher_name_display,
    get_subject_emoji,
)
from homeassistant import config_entries, core
from homeassistant.components.calendar import (
    CalendarEntity,
    CalendarEvent,
)
from homeassistant.util import Throttle
from homeassistant.util import dt as dt_util

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

    schoolschedule_enabled = config.get(CONF_SCHOOLSCHEDULE, True)
    ugeplan_enabled = config.get(CONF_UGEPLAN, True)
    meetings_enabled = config.get(CONF_MEETINGS, False)

    if not schoolschedule_enabled and not ugeplan_enabled and not meetings_enabled:
        async_add_entities([])
        return
    client = hass.data[DOMAIN]["client"]
    teacher_name_display = resolve_teacher_name_display(config)
    show_emoji = config.get(CONF_SCHOOLSCHEDULE_EMOJI, False)

    calendar_devices = []
    calendar = []

    # Discover each child's actual Aula class group.
    child_groups = await hass.async_add_executor_job(
        client.get_child_class_groups
    )

    for child in client._children:
        childid = child["id"]
        name = child["name"]

        if schoolschedule_enabled:
            # Existing school schedule
            calendar_devices.append(
                CalendarDevice(
                    hass,
                    calendar,
                    name,
                    childid,
                    teacher_name_display,
                    show_emoji,
                )
            )

            # Class birthday calendar
            group_info = child_groups.get(childid)

            if group_info:
                calendar_devices.append(
                    BirthdayCalendarDevice(
                        hass,
                        name,
                        childid,
                        group_info["group_id"],
                    )
                )
            else:
                _LOGGER.warning(
                    "No class group found for %s. "
                    "Birthday calendar will not be created.",
                    name,
                )

        if ugeplan_enabled:
            calendar_devices.append(
                UgeplanCalendarDevice(
                    hass,
                    name,
                    childid,
                )
            )

        if meetings_enabled:
            calendar_devices.append(
                MeetingCalendarDevice(
                    hass,
                    name,
                    childid,
                )
            )

    async_add_entities(calendar_devices)

class CalendarDevice(CalendarEntity):
    def __init__(
        self,
        hass,
        calendar,
        name,
        childid,
        teacher_name_display=TEACHER_NAME_INITIALS,
        show_emoji=False,
    ):
        self.data = CalendarData(hass, calendar, childid, teacher_name_display, show_emoji)
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
class BirthdayCalendarDevice(CalendarEntity):
    """Calendar containing classmates' birthdays."""

    def __init__(
        self,
        hass,
        child_name,
        childid,
        group_id,
    ):
        self._hass = hass
        self._client = hass.data[DOMAIN]["client"]

        self._childid = childid
        self._group_id = group_id

        # Use first name in event summaries
        self._child_name = child_name.split()[0]

        self._name = "Fødselsdage " + self._child_name

        self._birthdays = []
        self._event = None

    @property
    def name(self):
        """Return calendar name."""
        return self._name

    @property
    def unique_id(self):
        """Return unique entity ID."""
        return "aulabirthdays" + str(self._childid)

    @property
    def event(self):
        """Return next upcoming birthday."""
        return self._event

    def update(self):
        """Update birthday information."""

        self._birthdays = self._client.get_class_birthdays(
            self._group_id
        )

        self._event = self._find_next_event()

    async def async_get_events(
        self,
        hass,
        start_date,
        end_date,
    ):
        """Return birthday events for requested period."""

        birthdays = await hass.async_add_executor_job(
            self._client.get_class_birthdays,
            self._group_id,
        )

        return self._create_events(
            birthdays,
            start_date,
            end_date,
        )

    def _create_events(
        self,
        birthdays,
        start_date,
        end_date,
    ):
        """Create Home Assistant calendar events."""

        events = []

        if not birthdays:
            return events

        if isinstance(start_date, datetime):
            range_start = start_date.date()
        else:
            range_start = start_date

        if isinstance(end_date, datetime):
            range_end = end_date.date()
        else:
            range_end = end_date

        for contact in birthdays:
            try:
                birth_date = datetime.fromisoformat(
                    contact["birthday"].replace(
                        "Z",
                        "+00:00",
                    )
                ).date()

            except (ValueError, TypeError):
                _LOGGER.warning(
                    "Invalid birthday for %s: %s",
                    contact.get("name"),
                    contact.get("birthday"),
                )
                continue

            # Generate birthday for every requested year.
            for year in range(
                range_start.year,
                range_end.year + 1,
            ):
                try:
                    birthday_this_year = date(
                        year,
                        birth_date.month,
                        birth_date.day,
                    )

                except ValueError:
                    # Handle 29 February birthdays
                    if (
                        birth_date.month == 2
                        and birth_date.day == 29
                    ):
                        birthday_this_year = date(
                            year,
                            2,
                            28,
                        )
                    else:
                        continue

                if not (
                    range_start
                    <= birthday_this_year
                    < range_end
                ):
                    continue

                event_age = (
                    year - birth_date.year
                )

                summary = (
                    f"{self._child_name}: "
                    f"🎂 {contact['name']} "
                    f"({event_age})"
                )

                events.append(
                    CalendarEvent(
                        summary=summary,
                        start=birthday_this_year,
                        end=(
                            birthday_this_year
                            + timedelta(days=1)
                        ),
                    )
                )

        events.sort(
            key=lambda event: event.start
        )

        return events

    def _find_next_event(self):
        """Return next upcoming birthday."""

        if not self._birthdays:
            return None

        today = date.today()

        end = date(
            today.year + 1,
            12,
            31,
        )

        events = self._create_events(
            self._birthdays,
            today,
            end,
        )

        if events:
            return events[0]

        return None

class CalendarData:
    def __init__(
        self,
        hass,
        calendar,
        childid,
        teacher_name_display=TEACHER_NAME_INITIALS,
        show_emoji=False,
    ):
        self.event = None

        self._hass = hass
        self._calendar = calendar
        self._childid = childid
        self._teacher_name_display = teacher_name_display
        self._show_emoji = show_emoji

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
                event = parseCalendarLesson(c, self._teacher_name_display, self._show_emoji)
                events.append(event)
        return events

    async def async_get_events(self, hass, start_date, end_date):
        # Run file I/O in executor to avoid blocking the event loop
        all_events = await hass.async_add_executor_job(self.parseCalendarData)
        if not all_events:
            return []

        filtered_events = []
        for event in all_events:
            if event.end > start_date and event.start < end_date:
                filtered_events.append(event)

        return filtered_events

    @Throttle(MIN_TIME_BETWEEN_UPDATES)
    def update(self):
        _LOGGER.debug("Updating calendars...")
        self.parseCalendarData(self)


def parseCalendarLesson(lesson, teacher_name_display=TEACHER_NAME_INITIALS, show_emoji=False):
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
            if teacher_name_display == TEACHER_NAME_FULL:
                teacher = lesson["lesson"]["participants"][0]["teacherName"]
            elif teacher_name_display == TEACHER_NAME_FIRST_NAME_INITIALS:
                teacher_name = lesson["lesson"]["participants"][0]["teacherName"]
                teacher_initials = lesson["lesson"]["participants"][0]["teacherInitials"]
                teacher = f"{teacher_name.split(' ')[0]} ({teacher_initials})"
            else:
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
    if show_emoji:
        summary = f"{get_subject_emoji(summary)} {summary}"
    lesson = CalendarEvent(
        summary=str(summary) + ", " + str(teacher),
        start=start,
        end=end,
        location=location,
    )
    return lesson


class UgeplanCalendarDevice(CalendarEntity):
    """Calendar entity representing a child's weekly plan (Ugeplan)."""

    def __init__(
        self,
        hass,
        child_name,
        childid,
    ):
        self._hass = hass
        self._client = hass.data[DOMAIN]["client"]
        self._childid = childid
        self._first_name = child_name.split()[0]
        self._name = "Ugeplan " + self._first_name
        self._event = None

    @property
    def name(self):
        """Return calendar name."""
        return self._name

    @property
    def unique_id(self):
        """Return unique entity ID."""
        return "aula_ugeplan_" + str(self._childid)

    @property
    def event(self):
        """Return next upcoming event."""
        return self._event

    def update(self):
        """Update next upcoming event."""
        self._event = self._find_next_event()

    async def async_get_events(
        self,
        hass,
        start_date,
        end_date,
    ):
        """Return Ugeplan events for requested period."""
        return await hass.async_add_executor_job(
            self._get_events_for_period,
            start_date,
            end_date,
        )

    def _get_events_for_period(self, start_date, end_date):
        all_events = self._client.ugep_events.get(self._first_name, [])
        filtered = []

        for ev in all_events:
            if self._event_overlaps(ev.start, ev.end, start_date, end_date):
                filtered.append(ev)

        filtered.sort(key=self._event_sort_key)
        return filtered

    def _event_sort_key(self, ev):
        val = ev.start
        if isinstance(val, datetime):
            if val.tzinfo is not None:
                return val.astimezone(dt_util.DEFAULT_TIME_ZONE).replace(tzinfo=None)
            return val
        return datetime.combine(val, datetime.min.time())

    def _event_overlaps(self, ev_start, ev_end, start_date, end_date):
        def to_dt(val):
            if isinstance(val, datetime):
                return val
            return datetime.combine(val, datetime.min.time())

        s_dt = to_dt(ev_start)
        e_dt = to_dt(ev_end)
        start_dt = to_dt(start_date)
        end_dt = to_dt(end_date)

        if s_dt.tzinfo is not None and start_dt.tzinfo is None:
            s_dt = s_dt.replace(tzinfo=None)
            e_dt = e_dt.replace(tzinfo=None)
        elif s_dt.tzinfo is None and start_dt.tzinfo is not None:
            start_dt = start_dt.replace(tzinfo=None)
            end_dt = end_dt.replace(tzinfo=None)

        return e_dt > start_dt and s_dt < end_dt

    def _find_next_event(self):
        now = datetime.now()
        events = self._client.ugep_events.get(self._first_name, [])
        upcoming = []
        for ev in events:
            ev_start = ev.start
            if isinstance(ev_start, date) and not isinstance(ev_start, datetime):
                if ev_start >= now.date():
                    upcoming.append(ev)
            elif isinstance(ev_start, datetime):
                if ev_start.tzinfo is not None:
                    now_tz = datetime.now(ev_start.tzinfo)
                    if ev_start >= now_tz:
                        upcoming.append(ev)
                elif ev_start >= now:
                    upcoming.append(ev)
        if upcoming:
            upcoming.sort(key=self._event_sort_key)
            return upcoming[0]
        return None


def parse_meeting_bookings(meetings, childid, child_name=None):
    """Return one child's booked meeting times as calendar events.

    Aula meetings with a ``timeSlot`` are booked by parents: each slot is split
    into ``timeSlotIndexes`` (with gaps for breaks), and an answer whose
    ``concerningProfileId`` is the child points at the booked index. Both
    guardians usually answer for the same child, so the booking is de-duplicated.
    A meeting without time slots is a fixed appointment and is used as it is.
    Meetings the child has no booking in are left out. With ``child_name`` the
    summary starts with it, like the birthday calendar's, so meetings stay
    apart when several children's calendars are shown together.
    """
    events = []
    child = str(childid)
    for meeting in meetings:
        summary = meeting.get("title") or "Samtale"
        if child_name:
            summary = f"{child_name}: {summary}"
        description = "\n".join(
            part
            for part in (meeting.get("institutionName"), meeting.get("creatorName"))
            if part
        )
        location = (meeting.get("primaryResource") or {}).get("name") or meeting.get(
            "primaryResourceText"
        )

        def make_event(start, end):
            return CalendarEvent(
                summary=summary,
                start=datetime.fromisoformat(start),
                end=datetime.fromisoformat(end),
                description=description or None,
                location=location,
            )

        time_slot = meeting.get("timeSlot")
        if not time_slot:
            if child in (str(p) for p in meeting.get("belongsToProfiles") or []):
                events.append(
                    make_event(meeting["startDateTime"], meeting["endDateTime"])
                )
            continue

        booked = set()
        for slot in time_slot.get("timeSlots") or []:
            indexes = slot.get("timeSlotIndexes") or []
            for answer in slot.get("answers") or []:
                if str(answer.get("concerningProfileId")) != child:
                    continue
                index = answer.get("selectedTimeSlotIndex")
                if (slot.get("id"), index) in booked:
                    continue
                booked.add((slot.get("id"), index))
                if not isinstance(index, int) or not 0 <= index < len(indexes):
                    _LOGGER.warning(
                        "Aula meeting %s has a booking at unknown time slot index %s",
                        meeting.get("id"),
                        index,
                    )
                    continue
                events.append(
                    make_event(indexes[index]["startTime"], indexes[index]["endTime"])
                )

    events.sort(key=lambda event: event.start)
    return events


class MeetingCalendarDevice(CalendarEntity):
    """Calendar with a child's booked meetings (skole-hjem-samtaler etc.)."""

    def __init__(
        self,
        hass,
        child_name,
        childid,
    ):
        self._client = hass.data[DOMAIN]["client"]
        self._childid = childid
        self._first_name = child_name.split()[0]
        self._name = "Samtaler " + self._first_name
        self._event = None

    @property
    def name(self):
        """Return calendar name."""
        return self._name

    @property
    def unique_id(self):
        """Return unique entity ID."""
        return "aula_meetings_" + str(self._childid)

    @property
    def event(self):
        """Return the current or next meeting."""
        return self._event

    def _events(self):
        return parse_meeting_bookings(
            self._client.meetings, self._childid, self._first_name
        )

    def update(self):
        """Update the current or next meeting."""
        now = dt_util.now()
        self._event = next(
            (event for event in self._events() if event.end > now), None
        )

    async def async_get_events(self, hass, start_date, end_date):
        """Return meetings overlapping the requested period."""
        return [
            event
            for event in self._events()
            if event.end > start_date and event.start < end_date
        ]
