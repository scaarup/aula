import json
import os

from custom_components.aula.client import (
    docendo_plans_for_child,
    format_docendo_weekplans,
    parse_docendo_payload,
    ugeplan_to_data,
)


def test_empty_values_give_none():
    assert ugeplan_to_data(None) is None
    assert ugeplan_to_data("") is None
    assert ugeplan_to_data("   ") is None
    assert ugeplan_to_data("<br>") is None


def test_splits_at_headings_with_weekday():
    html = (
        "<h2>Uge 45</h2><h3>Vigtig information</h3><br><b>Tur</b> (<i>Anna</i>)<br>Tag tøj med<br>"
        "<br><h3>Mandag 03/11</h3><b>08:00-09:00 Dansk</b><br><i>Bo</i><br>Læs side 4<br>"
        "<h3>Tirsdag 04/11</h3><b>08:00-09:00 Matematik</b><br>"
    )
    data = ugeplan_to_data(html)
    headings = [s["heading"] for s in data["sections"]]
    assert headings == ["Uge 45", "Vigtig information", "Mandag 03/11", "Tirsdag 04/11"]
    general = data["sections"][1]
    assert "weekday" not in general
    assert general["text"] == "Tur (Anna)\nTag tøj med"
    assert data["sections"][2]["weekday"] == 0
    assert data["sections"][2]["text"] == "08:00-09:00 Dansk\nBo\nLæs side 4"
    assert data["sections"][3]["weekday"] == 1
    assert "<" not in json.dumps(data)


def test_text_before_first_heading_and_lists():
    data = ugeplan_to_data("<p>Hej alle</p><ul><li>Gym</li><li>Madpakke</li></ul><h3>Fredag</h3>Hyggedag")
    assert data["sections"][0] == {"heading": "", "text": "Hej alle\n- Gym\n- Madpakke"}
    assert data["sections"][1] == {"heading": "Fredag", "text": "Hyggedag", "weekday": 4}


def test_plain_text_without_headings():
    data = ugeplan_to_data("Ingen overskrifter her")
    assert data == {"sections": [{"heading": "", "text": "Ingen overskrifter her"}]}


def test_bold_lines_split_free_text_without_headings():
    html = (
        "<p><strong>Onsdag: Skakdag</strong></p><p><strong><u>Dansk</u></strong></p>"
        "<p><u>Mandag</u> har vi prøve.</p><p><strong><u>Historie</u></strong><br>Karl Gustav</p>"
        "<p>Vi <strong>skal</strong> have fri</p>"
    )
    data = ugeplan_to_data(html)
    assert [(s["heading"], s["text"]) for s in data["sections"]] == [
        ("Onsdag: Skakdag", ""),
        ("Dansk", "Mandag har vi prøve."),
        ("Historie", "Karl Gustav\nVi skal have fri"),
    ]


def test_bold_lines_are_not_headings_when_real_headings_exist():
    data = ugeplan_to_data("<h3>Mandag</h3><b>08:00 Dansk</b><br>Læs")
    assert len(data["sections"]) == 1
    assert data["sections"][0]["text"] == "08:00 Dansk\nLæs"


def test_script_is_dropped_and_result_is_json_serialisable():
    data = ugeplan_to_data("<h3>Mandag</h3>Hej<script>alert(1)</script>")
    assert "alert" not in json.dumps(data)
    json.dumps(data)


def test_docendo_output():
    path = os.path.join(os.path.dirname(__file__), "fixtures", "docendo_weekplan.json")
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    plans = parse_docendo_payload(payload)
    html = format_docendo_weekplans(plans)
    data = ugeplan_to_data(html)
    assert data is not None and data["sections"]
    assert all(isinstance(s["text"], str) for s in data["sections"])
    assert any("weekday" in s for s in data["sections"])
