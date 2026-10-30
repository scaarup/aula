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


@pytest.mark.parametrize("token", ["not-a-jwt", None, fake_jwt({"sub": 42}), fake_jwt({"sub": ""}), fake_jwt({})])
def test_jwt_subject__invalid_or_missing_sub(token):
    assert jwt_subject(token) is None


def test_plans_for_child__matches_class_ignoring_case_and_punctuation(plans):
    assert docendo_plans_for_child(plans, " 4a ") == ([plans[0]], True)
    assert docendo_plans_for_child(plans, "4.A") == ([plans[0]], True)
    assert docendo_plans_for_child(plans, "0 B") == ([plans[1]], True)


def test_plans_for_child__unmatched_returns_all_plans_unmatched(plans):
    assert docendo_plans_for_child(plans, "9Z") == (plans, False)
    assert docendo_plans_for_child(plans, None) == (plans, False)


def test_parse_payload__returns_plans(payload):
    assert len(parse_docendo_payload(payload)) == 2


@pytest.mark.parametrize("bad", [None, [], {"data": None}, {"data": {"weekPlans": None}}, {"message": "Unauthorized"}])
def test_parse_payload__rejects_unexpected_shapes(bad):
    with pytest.raises(ValueError):
        parse_docendo_payload(bad)


def test_parse_payload__error_does_not_contain_content():
    with pytest.raises(ValueError) as err:
        parse_docendo_payload({"data": {"weekPlans": {"name": "4A", "note": "Hemmelig note"}}})
    assert "Hemmelig" not in str(err.value) and "4A" not in str(err.value)
    assert "weekPlans is dict" in str(err.value)


def test_parse_payload__drops_non_dict_plans():
    assert parse_docendo_payload({"data": {"weekPlans": [{"name": "1A"}, "junk", None]}}) == [{"name": "1A"}]


def test_parse_time__utc_and_naive_are_utc():
    expected = datetime.datetime(2026, 10, 26, 8, 0)
    for value in ("2026-10-26T07:00:00Z", "2026-10-26T07:00:00"):
        parsed = parse_docendo_time(value)
        assert parsed.tzinfo is not None
        assert parsed.replace(tzinfo=None) == expected


def test_parse_time__summer_time():
    assert parse_docendo_time("2026-10-23T07:00:00Z").hour == 9


@pytest.mark.parametrize("value", [None, "", 123, "not a date"])
def test_parse_time__invalid_values(value):
    assert parse_docendo_time(value) is None


def test_lessons__skips_empty_and_sorts(plans):
    assert [ev["title"] for ev, _, _ in docendo_lessons(plans[0])] == ["Dansk", "Matematik"]


@pytest.mark.parametrize("end_time", [None, "2026-10-26T07:00:00Z", "2026-10-26T06:00:00Z", "garbage"])
def test_lessons__end_not_after_start_gets_default_duration(end_time):
    plan = {"events": [{"title": "X", "description": "d", "startTime": "2026-10-26T07:00:00Z", "endTime": end_time}]}
    ((_, start, end),) = docendo_lessons(plan)
    assert end - start == datetime.timedelta(minutes=45)
    (event,) = docendo_calendar_events([plan])
    assert event.end > event.start


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


@pytest.mark.parametrize(
    "dirty",
    [
        "<![CDATA[ > <img src=x onerror=alert(1)> ]]>",
        "<!-- --!> <img src=x onerror=alert(1)> -->",
        "<!x <img src=x onerror=alert(1)>>",
        "<?php <img src=x onerror=alert(1)> ?>",
    ],
)
def test_sanitize_html__drops_comments_and_cdata(dirty):
    assert "onerror" not in sanitize_html(dirty)


def test_format__learning_goal_label():
    plan = {"name": "4A", "events": [{"title": "Dansk", "learningGoal": "<p>Mål</p>", "startTime": "2026-10-26T07:00:00Z"}]}
    assert "<i>Læringsmål:</i> <p>Mål</p>" in format_docendo_weekplans([plan])


def test_format__groups_by_local_day():
    plan = {"name": "4A", "events": [{"title": "Sen", "description": "x", "startTime": "2026-10-26T23:30:00Z"}]}
    assert "<h3>tirsdag 27/10</h3>" in format_docendo_weekplans([plan])


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
    # Mirrors the Docendo state set up in Client.__init__.
    client = object.__new__(Client)
    client._children = children
    client._mitid_username = "mitid-user"
    client._docendo_cache = {}
    client._docendo_session = FakeSession(responses)
    client._docendo_children = set()
    client._docendo_failing = False
    client.ugep_attr = {}
    client.ugepnext_attr = {}
    client.ugep_events = {}
    client.tokens = {}
    jwt = jwt or fake_jwt({"sub": "aula-user"})
    client.get_token = lambda widget: "Bearer " + jwt
    return client


def child(child_id, name, class_name):
    return {"id": child_id, "name": name, "institutionProfile": {"metadata": class_name}}


def both_weeks(body):
    return {"2026-W44": FakeResponse(body=body), "2026-W45": FakeResponse(body=body)}


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


def test_fetch__refetches_after_cache_expires(payload):
    client = make_client([], {"2026-W44": FakeResponse(body=payload)})
    plans = client.fetch_docendo_weekplans("2026-W44")
    client._docendo_cache["2026-W44"] = (dt_util.utcnow() - datetime.timedelta(minutes=31), plans)
    client.fetch_docendo_weekplans("2026-W44")
    assert len(client._docendo_session.calls) == 2


def test_fetch__no_token_returns_none(payload):
    client = make_client([], {"2026-W44": FakeResponse(body=payload)})
    client.get_token = lambda widget: None
    assert client.fetch_docendo_weekplans("2026-W44") is None
    assert client._docendo_session.calls == []


@pytest.mark.parametrize("status", [401, 403])
def test_fetch__rejected_token_is_evicted(status):
    client = make_client([], {"2026-W44": FakeResponse(status, {"message": "Unauthorized"})})
    client.tokens[DOCENDO_WIDGET] = ("Bearer x", dt_util.utcnow())
    assert client.fetch_docendo_weekplans("2026-W44") is None
    assert DOCENDO_WIDGET not in client.tokens


@pytest.mark.parametrize(
    "failure",
    [
        requests.ConnectionError("down"),
        FakeResponse(503),
        FakeResponse(body=requests.exceptions.JSONDecodeError("Expecting value", "<html>", 0)),
        FakeResponse(body={"data": None}),
    ],
)
def test_fetch__errors_fall_back_to_cached_plans(payload, failure):
    client = make_client([], {"2026-W44": FakeResponse(body=payload)})
    plans = client.fetch_docendo_weekplans("2026-W44")
    client._docendo_cache["2026-W44"] = (dt_util.utcnow() - datetime.timedelta(hours=1), plans)
    client._docendo_session.responses["2026-W44"] = failure
    assert client.fetch_docendo_weekplans("2026-W44") == plans


def test_fetch__warns_once_per_outage(payload, caplog):
    client = make_client([], {"2026-W44": FakeResponse(503)})
    with caplog.at_level("DEBUG"):
        for _ in range(3):
            client.fetch_docendo_weekplans("2026-W44")
        client._docendo_session.responses["2026-W44"] = FakeResponse(body=payload)
        client.fetch_docendo_weekplans("2026-W44")
    levels = [r.levelname for r in caplog.records if "Docendo" in r.getMessage()]
    assert levels.count("WARNING") == 1
    assert "Docendo ugeplan is available again" in caplog.text


def test_update__writes_only_matching_children_and_leaves_siblings(payload):
    children = [child(1, "Alma Hansen", "4A"), child(2, "Bo Hansen", "0B"), child(3, "Carl Hansen", "7C")]
    client = make_client(children, both_weeks(payload))
    sibling_event = CalendarEvent(summary="EasyIQ", start=datetime.date(2026, 10, 26), end=datetime.date(2026, 10, 27))
    client.ugep_attr["Carl"] = "from another provider"
    client.ugep_events["Carl"] = [sibling_event]

    client.update_docendo_weekplans("2026-W44", "2026-W45")

    assert "Ugeplan for 4A" in client.ugep_attr["Alma"] and "Ugeplan for 0B" not in client.ugep_attr["Alma"]
    assert "Ugeplan for 0B" in client.ugepnext_attr["Bo"]
    assert client.ugep_attr["Carl"] == "from another provider"
    assert client.ugep_events["Carl"] == [sibling_event]


def test_update__events_cover_both_weeks_without_duplicates(payload):
    client = make_client([child(1, "Alma Hansen", "4A")], both_weeks(payload))
    client.update_docendo_weekplans("2026-W44", "2026-W45")
    client._docendo_cache.clear()
    client.update_docendo_weekplans("2026-W44", "2026-W45")
    # Same fixture for both weeks: 2 lessons per week, rebuilt (not appended) each cycle.
    assert [ev.summary for ev in client.ugep_events["Alma"]] == ["Dansk", "Matematik", "Dansk", "Matematik"]


def test_update__this_week_failure_does_not_duplicate_events(payload):
    client = make_client([child(1, "Alma Hansen", "4A")], {"2026-W44": FakeResponse(503), "2026-W45": FakeResponse(body=payload)})
    for _ in range(3):
        client._docendo_cache.clear()
        client.update_docendo_weekplans("2026-W44", "2026-W45")
    assert len(client.ugep_events["Alma"]) == 2
    assert "Alma" not in client.ugep_attr


def test_update__both_weeks_failing_leaves_state_alone():
    client = make_client([child(1, "Alma Hansen", "4A")], {"2026-W44": FakeResponse(503), "2026-W45": FakeResponse(503)})
    client.ugep_attr["Alma"] = "old"
    client.update_docendo_weekplans("2026-W44", "2026-W45")
    assert client.ugep_attr == {"Alma": "old"} and client.ugep_events == {}


def test_update__empty_week_clears_previous_docendo_plan(payload):
    client = make_client([child(1, "Alma Hansen", "4A")], both_weeks(payload))
    client.update_docendo_weekplans("2026-W44", "2026-W45")
    empty = {"data": {"weekPlans": []}}
    client._docendo_cache.clear()
    client._docendo_session.responses.update({"2026-W44": FakeResponse(body=empty), "2026-W45": FakeResponse(body=empty)})
    client.update_docendo_weekplans("2026-W44", "2026-W45")
    assert client.ugep_attr["Alma"] == "" and client.ugepnext_attr["Alma"] == ""
    assert client.ugep_events["Alma"] == []


def test_update__empty_week_does_not_touch_other_providers():
    client = make_client([child(1, "Alma Hansen", "4A"), child(2, "Bo Hansen", "0B")], both_weeks({"data": {"weekPlans": []}}))
    client.ugep_attr["Alma"] = "from another provider"
    client.update_docendo_weekplans("2026-W44", "2026-W45")
    assert client.ugep_attr == {"Alma": "from another provider"}


def test_update__single_child_gets_unmatched_plans(payload):
    client = make_client([child(1, "Alma Hansen", None)], both_weeks(payload))
    client.update_docendo_weekplans("2026-W44", "2026-W45")
    assert "Ugeplan for 4A" in client.ugep_attr["Alma"] and "Ugeplan for 0B" in client.ugep_attr["Alma"]


@pytest.mark.parametrize("name", [None, "", "   "])
def test_update__child_without_name_uses_id(payload, name):
    client = make_client([child(42, name, "4A")], both_weeks(payload))
    client.update_docendo_weekplans("2026-W44", "2026-W45")
    assert "Ugeplan for 4A" in client.ugep_attr["42"]


