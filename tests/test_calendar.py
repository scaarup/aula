import os
import pytest
import json

from custom_components.aula.calendar import (
    parseCalendarLesson,
)
from custom_components.aula.const import (
    TEACHER_NAME_INITIALS,
    TEACHER_NAME_FULL,
    TEACHER_NAME_FIRST_NAME_INITIALS,
    DEFAULT_SUBJECT_EMOJI,
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


@pytest.fixture
def sample__normal():
    return load_json_fixture("calendar_lesson_normal.json")


def test_parse__with_substitute_with_location(sample__substitute_with_location):
    event = parseCalendarLesson(sample__substitute_with_location)
    assert event.summary == "Test Subject, VIKAR: Test Substitute"
    assert event.location == "Test Location"


def test_parse__with_substitute_without_location(sample__substitute_without_location):
    event = parseCalendarLesson(sample__substitute_without_location)
    assert event.summary == "Test Subject, VIKAR: Test Substitute"
    assert event.location == None


def test_parse__teacher_initials(sample__normal):
    event = parseCalendarLesson(sample__normal, TEACHER_NAME_INITIALS)
    assert event.summary == "Test Subject, JB"


def test_parse__teacher_full_name(sample__normal):
    event = parseCalendarLesson(sample__normal, TEACHER_NAME_FULL)
    assert event.summary == "Test Subject, Jesper Balle"


def test_parse__teacher_first_name_initials(sample__normal):
    event = parseCalendarLesson(sample__normal, TEACHER_NAME_FIRST_NAME_INITIALS)
    assert event.summary == "Test Subject, Jesper (JB)"


def test_parse__default_is_initials(sample__normal):
    event = parseCalendarLesson(sample__normal)
    assert event.summary == "Test Subject, JB"


def test_parse__emoji_off_by_default(sample__normal):
    event = parseCalendarLesson(sample__normal, TEACHER_NAME_INITIALS, False)
    assert event.summary == "Test Subject, JB"


def test_parse__emoji_matches_known_subject(sample__normal):
    sample__normal["title"] = "Dansk"
    event = parseCalendarLesson(sample__normal, TEACHER_NAME_INITIALS, True)
    assert event.summary == "📖 Dansk, JB"


def test_parse__emoji_falls_back_to_default_for_unknown_subject(sample__normal):
    event = parseCalendarLesson(sample__normal, TEACHER_NAME_INITIALS, True)
    assert event.summary == f"{DEFAULT_SUBJECT_EMOJI} Test Subject, JB"


def test_parse__emoji_applies_to_substitute_teacher(sample__substitute_with_location):
    sample__substitute_with_location["title"] = "Matematik"
    event = parseCalendarLesson(sample__substitute_with_location, show_emoji=True)
    assert event.summary == "🔢 Matematik, VIKAR: Test Substitute"
