import base64
import datetime
import json
import os

import pytest
import requests
from homeassistant.components.calendar import CalendarEvent
from homeassistant.util import dt as dt_util

from custom_components.aula.client import (
    DOCENDO_WIDGET,
    Client,
    docendo_calendar_events,
    docendo_lessons,
    docendo_plans_for_child,
    format_docendo_weekplans,
    jwt_subject,
    parse_docendo_payload,
    parse_docendo_time,
    sanitize_html,
)


def load_json_fixture(filename):
    fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", filename)
    with open(fixture_path, encoding="utf-8") as f:
        return json.load(f)


def fake_jwt(claims):
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return "header." + payload + ".signature"


@pytest.fixture(autouse=True)
def copenhagen_time_zone():
    original = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Copenhagen"))
    yield
    dt_util.set_default_time_zone(original)


@pytest.fixture
def payload():
    return load_json_fixture("docendo_weekplan.json")


@pytest.fixture
def plans(payload):
    return payload["data"]["weekPlans"]


# Helpers


def test_jwt_subject():
    assert jwt_subject(fake_jwt({"sub": "abcd12ef"})) == "abcd12ef"


def test_jwt_subject__invalid_token():
    assert jwt_subject("not-a-jwt") is None


def test_plans_for_child__matches_class_ignoring_case_and_punctuation(plans):
    assert docendo_plans_for_child(plans, " 4a ") == ([plans[0]], True)
    assert docendo_plans_for_child(plans, "4.A") == ([plans[0]], True)
    assert docendo_plans_for_child(plans, "0 B") == ([plans[1]], True)


def test_plans_for_child__unmatched_returns_all_plans_unmatched(plans):
    assert docendo_plans_for_child(plans, "9Z") == (plans, False)
    assert docendo_plans_for_child(plans, None) == (plans, False)


def test_parse_payload__rejects_unexpected_shapes(payload):
    assert len(parse_docendo_payload(payload)) == 2
    for bad in (None, [], {"data": None}, {"data": {"weekPlans": None}}, {"message": "Unauthorized"}):
        with pytest.raises(ValueError):
            parse_docendo_payload(bad)


def test_parse_payload__drops_non_dict_plans():
    assert parse_docendo_payload({"data": {"weekPlans": [{"name": "1A"}, "junk", None]}}) == [{"name": "1A"}]


def test_parse_time__utc_and_naive_are_utc():
    expected = datetime.datetime(2026, 10, 26, 8, 0)
    for value in ("2026-10-26T07:00:00Z", "2026-10-26T07:00:00"):
        parsed = parse_docendo_time(value)
        assert parsed.tzinfo is not None
        assert parsed.replace(tzinfo=None) == expected


def test_parse_time__invalid_values():
    for value in (None, "", 123, "not a date"):
        assert parse_docendo_time(value) is None


def test_lessons__skips_empty_and_sorts(plans):
    assert [ev["title"] for ev, _, _ in docendo_lessons(plans[0])] == ["Dansk", "Matematik"]


def test_lessons__tolerates_malformed_events():
    plan = {"events": [None, "junk", {"title": "X", "description": 5}, {"title": "Y", "description": "<p>ok</p>", "startTime": None}]}
    assert docendo_lessons(plan) == []


def test_sanitize_html__removes_scripts_and_handlers():
    dirty = '<p onclick="x()">Hej<script>alert(1)</script><img src=x onerror=alert(1)><a href="javascript:alert(1)">l</a><a href="https://example.com" target="_blank">ok</a></p>'
    clean = sanitize_html(dirty)
    assert "script" not in clean
    assert "onerror" not in clean and "onclick" not in clean and "<img" not in clean
    assert "javascript:" not in clean
    assert '<a href="https://example.com">ok</a>' in clean
    assert clean.startswith("<p>Hej")


def test_format__converts_utc_to_local_time(plans):
    html = format_docendo_weekplans([plans[0]])
    assert "<h2>Ugeplan for 4A</h2>" in html
    assert "<p>Note til ugen for 4A</p>" in html
    assert "<h3>mandag 26/10</h3>" in html
    assert "<b>Dansk</b> 08:00-09:30" in html
    assert "<i>Elevforberedelse:</i> <p>Husk biblioteksbøger</p>" in html
    assert "Fællespause" not in html


def test_format__escapes_name_and_title():
    plan = {
        "name": "<b>4A</b>",
        "events": [{"title": "<img src=x onerror=alert(1)>", "description": "<p>x</p>", "startTime": "2026-10-26T07:00:00Z"}],
    }
    html = format_docendo_weekplans([plan])
    assert "&lt;b&gt;4A&lt;/b&gt;" in html
    assert "<img" not in html


def test_calendar_events(plans):
    events = docendo_calendar_events(plans)
    assert [ev.summary for ev in events] == ["Dansk (4A)", "Matematik (4A)", "0B MAT (0B)"]
    dansk = events[0]
    assert dansk.start.replace(tzinfo=None) == datetime.datetime(2026, 10, 26, 8, 0)
    assert dansk.description == "Vi læser højt\n\nElevforberedelse: Husk biblioteksbøger"
    assert events[1].description == "Brøker & decimaltal"


def test_calendar_events__single_plan_has_no_class_suffix(plans):
    assert [ev.summary for ev in docendo_calendar_events([plans[1]])] == ["0B MAT"]


# Client.fetch_docendo_weekplans / update_docendo_weekplans


class FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Error")

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url, params=None, headers=None, **kwargs):
        self.calls.append({"url": url, "params": params, "headers": headers, **kwargs})
        response = self.responses[params["currentWeekNumber"]]
        if isinstance(response, Exception):
            raise response
        return response


def make_client(children, responses, jwt=None):
    client = object.__new__(Client)
    client._children = children
    client._mitid_username = "mitid-user"
    client._docendo_cache = {}
    client._docendo_session = FakeSession(responses)
    client.ugep_attr = {}
    client.ugepnext_attr = {}
    client.ugep_events = {}
    jwt = jwt or fake_jwt({"sub": "aula-user"})
    client.tokens = {DOCENDO_WIDGET: ("Bearer " + jwt, datetime.datetime.now(datetime.timezone.utc))}
    return client


def child(name, class_name):
    return {"id": hash(name), "name": name, "institutionProfile": {"metadata": class_name}}


def test_fetch__sends_widget_headers(payload):
    client = make_client([], {"2026-W44": FakeResponse(body=payload)})
    assert len(client.fetch_docendo_weekplans("2026-W44")) == 2
    call = client._docendo_session.calls[0]
    assert call["url"] == "https://app.docendo.dk/aula/v2/weekplan"
    assert call["params"] == {"currentWeekNumber": "2026-W44", "userProfile": "guardian"}
    assert call["headers"]["authorization"].startswith("Baerer header.")
    assert call["headers"]["aulasessionuuid"] == "aula-user"


def test_fetch__omits_session_uuid_without_sub(payload):
    client = make_client([], {"2026-W44": FakeResponse(body=payload)}, jwt=fake_jwt({}))
    client.fetch_docendo_weekplans("2026-W44")
    assert "aulasessionuuid" not in client._docendo_session.calls[0]["headers"]


def test_fetch__caches_per_week(payload):
    client = make_client([], {"2026-W44": FakeResponse(body=payload)})
    client.fetch_docendo_weekplans("2026-W44")
    client.fetch_docendo_weekplans("2026-W44")
    assert len(client._docendo_session.calls) == 1


def test_fetch__unauthorized_evicts_token_and_returns_none():
    client = make_client([], {"2026-W44": FakeResponse(401, {"message": "Unauthorized"})})
    assert client.fetch_docendo_weekplans("2026-W44") is None
    assert DOCENDO_WIDGET not in client.tokens


def test_fetch__errors_fall_back_to_cached_plans(payload):
    client = make_client([], {"2026-W44": FakeResponse(body=payload)})
    plans = client.fetch_docendo_weekplans("2026-W44")
    stale = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
    client._docendo_cache["2026-W44"] = (stale, plans)
    for failure in (requests.ConnectionError("down"), FakeResponse(body=ValueError("not json")), FakeResponse(body={"data": None})):
        client._docendo_session.responses["2026-W44"] = failure
        assert client.fetch_docendo_weekplans("2026-W44") == plans


def test_update__writes_only_matching_children_and_leaves_siblings(payload):
    children = [child("Alma Hansen", "4A"), child("Bo Hansen", "0B"), child("Carl Hansen", "7C")]
    client = make_client(children, {"2026-W44": FakeResponse(body=payload), "2026-W45": FakeResponse(body=payload)})
    sibling_event = CalendarEvent(summary="EasyIQ", start=datetime.date(2026, 10, 26), end=datetime.date(2026, 10, 27))
    client.ugep_attr["Carl"] = "from another provider"
    client.ugep_events["Carl"] = [sibling_event]

    client.update_docendo_weekplans("2026-W44", "2026-W45")

    assert "Ugeplan for 4A" in client.ugep_attr["Alma"] and "0B" not in client.ugep_attr["Alma"]
    assert "Ugeplan for 0B" in client.ugepnext_attr["Bo"]
    assert client.ugep_attr["Carl"] == "from another provider"
    assert client.ugep_events["Carl"] == [sibling_event]


def test_update__events_cover_both_weeks_without_duplicates(payload):
    client = make_client([child("Alma Hansen", "4A")], {"2026-W44": FakeResponse(body=payload), "2026-W45": FakeResponse(body=payload)})
    client.update_docendo_weekplans("2026-W44", "2026-W45")
    client._docendo_cache.clear()
    client.update_docendo_weekplans("2026-W44", "2026-W45")
    # Same fixture for both weeks: 2 lessons per week, rebuilt (not appended) each cycle.
    assert [ev.summary for ev in client.ugep_events["Alma"]] == ["Dansk", "Matematik", "Dansk", "Matematik"]


def test_update__this_week_failure_does_not_duplicate_events(payload):
    client = make_client([child("Alma Hansen", "4A")], {"2026-W44": FakeResponse(503), "2026-W45": FakeResponse(body=payload)})
    for _ in range(3):
        client._docendo_cache.clear()
        client.tokens[DOCENDO_WIDGET] = ("Bearer " + fake_jwt({"sub": "u"}), datetime.datetime.now(datetime.timezone.utc))
        client.update_docendo_weekplans("2026-W44", "2026-W45")
    assert len(client.ugep_events["Alma"]) == 2
    assert "Alma" not in client.ugep_attr


def test_update__single_child_gets_unmatched_plans(payload):
    client = make_client([child("Alma Hansen", None)], {"2026-W44": FakeResponse(body=payload), "2026-W45": FakeResponse(body=payload)})
    client.update_docendo_weekplans("2026-W44", "2026-W45")
    assert "Ugeplan for 4A" in client.ugep_attr["Alma"] and "Ugeplan for 0B" in client.ugep_attr["Alma"]
