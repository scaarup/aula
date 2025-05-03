import os
import pytest
import json

from custom_components.aula.const import DOMAIN
from custom_components.aula.calendar import (
    parseCalendarLesson,
)


def load_json_fixture(filename):
    fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", filename)
    with open(fixture_path) as f:
        return json.load(f)


@pytest.fixture
def sample__substitute_with_location():
    return load_json_fixture("calendar_lesson_substitute_with_location.json")


@pytest.fixture
def sample__substitute_without_location():
    return load_json_fixture("calendar_lesson_substitute_without_location.json")


def test_parse__with_substitute_with_location(sample__substitute_with_location):
    event = parseCalendarLesson(sample__substitute_with_location)
    assert event.summary == "Test Subject, VIKAR: Test Substitute"
    assert event.location == "Test Location"


def test_parse__with_substitute_without_location(sample__substitute_without_location):
    event = parseCalendarLesson(sample__substitute_without_location)
    assert event.summary == "Test Subject, VIKAR: Test Substitute"
    assert event.location == None
