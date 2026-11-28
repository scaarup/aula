import datetime
import json
import os

import pytest

from custom_components.aula.client import parse_skolemad_wallets, skolemad_week_start
from custom_components.aula.const import SKOLEMAD_API, SKOLEMAD_WIDGET


def load_json_fixture(filename):
    fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", filename)
    with open(fixture_path) as f:
        return json.load(f)


@pytest.fixture
def sample_wallets():
    return load_json_fixture("skolemad_wallets.json")


def test_constants():
    assert SKOLEMAD_WIDGET == "0118"
    assert SKOLEMAD_API.startswith("https://")


def test_parse__keys_by_first_name_and_sorts_dates(sample_wallets):
    result = parse_skolemad_wallets(sample_wallets)
    assert list(result) == ["Emilie", "Oscar"]
    assert list(result["Emilie"]) == ["2026-11-23", "2026-11-24", "2026-11-25"]


def test_parse__joins_dishes_and_marks_not_ordered(sample_wallets):
    emilie = parse_skolemad_wallets(sample_wallets)["Emilie"]
    assert emilie["2026-11-23"] == "Pasta med kødsovs"
    assert emilie["2026-11-24"] == "Kyllingesuppe; Rugbrød"
    assert emilie["2026-11-25"] is None


def test_parse__child_without_dates(sample_wallets):
    assert parse_skolemad_wallets(sample_wallets)["Oscar"] == {}


@pytest.mark.parametrize("data", [None, {}, {"Wallets": None}, {"Wallets": []}])
def test_parse__empty_responses(data):
    assert parse_skolemad_wallets(data) == {}


def test_week_start__midweek():
    tz = datetime.timezone(datetime.timedelta(hours=1))
    now = datetime.datetime(2026, 11, 26, 14, 30, tzinfo=tz)  # Thursday
    assert skolemad_week_start(now) == datetime.datetime(2026, 11, 23, tzinfo=tz)


def test_week_start__monday_and_sunday():
    monday = datetime.datetime(2026, 11, 23, 0, 0, 1)
    sunday = datetime.datetime(2026, 11, 29, 23, 59)
    assert skolemad_week_start(monday) == datetime.datetime(2026, 11, 23)
    assert skolemad_week_start(sunday) == datetime.datetime(2026, 11, 23)
