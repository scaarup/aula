import datetime
import json
import os

import pytest

from custom_components.aula.calendar import parse_meeting_bookings
from custom_components.aula.client import calendar_date_chunks
from custom_components.aula.const import CALENDAR_MAX_SPAN_DAYS

UTC = datetime.timezone.utc


@pytest.fixture
def meetings():
    path = os.path.join(os.path.dirname(__file__), "fixtures", "calendar_meetings.json")
    with open(path) as f:
        return json.load(f)["data"]


def test_booked_slot_is_the_selected_index(meetings):
    # Index 9 is 15:45 even though the slot has a 14:45-15:00 break: the time
    # comes from timeSlotIndexes, not from slot start + 9 x 15 minutes.
    events = parse_meeting_bookings(meetings[:1], 1001)
    assert len(events) == 1
    assert events[0].summary == "Skole-hjem-samtale"
    assert events[0].start == datetime.datetime(2026, 11, 23, 15, 45, tzinfo=UTC)
    assert events[0].end == datetime.datetime(2026, 11, 23, 16, 0, tzinfo=UTC)
    assert events[0].description == "Test Skole\nTest Teacher"


def test_both_guardians_answering_gives_one_event(meetings):
    assert len(parse_meeting_bookings(meetings[:1], 1001)) == 1


def test_other_childrens_bookings_are_ignored(meetings):
    events = parse_meeting_bookings(meetings, 1002)
    assert [e.start for e in events] == [
        datetime.datetime(2026, 11, 16, 15, 0, tzinfo=UTC)
    ]


def test_child_id_may_be_a_string(meetings):
    assert len(parse_meeting_bookings(meetings[:1], "1001")) == 1


def test_meeting_without_a_booking_is_left_out(meetings):
    assert parse_meeting_bookings(meetings[1:2], 1001) == []


def test_meeting_without_time_slots_uses_its_own_time(meetings):
    events = parse_meeting_bookings(meetings[2:], 1001)
    assert len(events) == 1
    assert events[0].summary == "Samtale med AKT"
    assert events[0].start == datetime.datetime(2026, 12, 1, 8, 0, tzinfo=UTC)
    assert events[0].location == "Lokale 12"


def test_fixed_meeting_for_another_child_is_left_out(meetings):
    assert parse_meeting_bookings(meetings[2:], 1003) == []


def test_events_are_sorted_by_start(meetings):
    events = parse_meeting_bookings(list(reversed(meetings)), 1001)
    assert [e.summary for e in events] == ["Skole-hjem-samtale", "Samtale med AKT"]


def test_unknown_slot_index_is_skipped(meetings, caplog):
    meetings[0]["timeSlot"]["timeSlots"][1]["answers"][0]["selectedTimeSlotIndex"] = 42
    meetings[0]["timeSlot"]["timeSlots"][1]["answers"][1]["selectedTimeSlotIndex"] = 42
    assert parse_meeting_bookings(meetings[:1], 1001) == []
    assert "unknown time slot index 42" in caplog.text


def test_parental_meeting_in_a_kindergarten(meetings):
    events = parse_meeting_bookings(meetings, 1003)
    assert [(e.summary, e.start) for e in events] == [
        ("Forældresamtale", datetime.datetime(2026, 10, 27, 11, 0, tzinfo=UTC)),
        ("Skole-hjem-samtale", datetime.datetime(2026, 11, 23, 14, 0, tzinfo=UTC)),
    ]


def test_date_chunks_respect_aulas_span_limit():
    start = datetime.date(2026, 10, 5)
    chunks = calendar_date_chunks(start, 100)
    assert chunks == [
        (start, datetime.date(2026, 11, 24)),
        (datetime.date(2026, 11, 24), datetime.date(2027, 1, 13)),
    ]
    assert all((end - begin).days <= CALENDAR_MAX_SPAN_DAYS for begin, end in chunks)


def test_date_chunks_last_chunk_is_shortened():
    start = datetime.date(2026, 1, 1)
    assert calendar_date_chunks(start, 60)[-1] == (
        datetime.date(2026, 2, 20),
        datetime.date(2026, 3, 2),
    )


def test_date_chunks_short_window_is_one_request():
    start = datetime.date(2026, 1, 1)
    assert calendar_date_chunks(start, 14) == [(start, datetime.date(2026, 1, 15))]


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.cookies = self

    def get_dict(self):
        return {}

    def post(self, url, json=None, headers=None, verify=True):
        self.requests.append(json)
        return FakeResponse(self.responses.pop(0))


def make_client(responses):
    from custom_components.aula.client import Client

    client = Client.__new__(Client)
    client._session = FakeSession(responses)
    client._childids = ["1001", "1003"]
    client._tokens = {"access_token": "token"}
    client.apiurl = "https://www.aula.dk/api/v24"
    client.meetings = []
    client._meetings_fetched_at = None
    return client


def ok(*events):
    return {"status": {"code": 0, "message": "OK"}, "data": list(events)}


def test_update_meetings_keeps_only_meetings_and_dedupes_across_chunks(meetings):
    lesson = {"id": 99, "type": "lesson"}
    client = make_client([ok(meetings[0], lesson), ok(meetings[0], meetings[2])])
    client._update_meetings()
    assert sorted(m["id"] for m in client.meetings) == [1, 3]
    first, second = client._session.requests
    assert first["instProfileIds"] == [1001, 1003]
    assert first["end"] == second["start"]


def test_update_meetings_keeps_previous_meetings_when_aula_refuses(meetings):
    client = make_client([ok(meetings[0]), {"status": {"code": 403}, "data": None}])
    client.meetings = [meetings[2]]
    client._update_meetings()
    assert client.meetings == [meetings[2]]
    assert client._meetings_fetched_at is None


def test_update_meetings_is_throttled(meetings):
    client = make_client([ok(meetings[0]), ok()])
    client._update_meetings()
    client._update_meetings()
    assert len(client._session.requests) == 2
