import os
import pytest
import json

from custom_components.aula.client import (
    MU_OPGAVER_WIDGETS,
    decode_mu_deeplink,
    format_mu_opgaver,
)


def load_json_fixture(filename):
    fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", filename)
    with open(fixture_path) as f:
        return json.load(f)


@pytest.fixture
def sample__opgaveliste():
    return load_json_fixture("mu_opgaveliste.json")["opgaver"]


def test_widget_preference_order():
    assert MU_OPGAVER_WIDGETS == ("0030", "0023")


def test_widget_selection__prefers_0030():
    widgets = {"0023": "MinUddannelse - SSO", "0030": "MU Opgaver"}
    selected = next((w for w in MU_OPGAVER_WIDGETS if w in widgets), None)
    assert selected == "0030"


def test_widget_selection__falls_back_to_0023():
    widgets = {"0023": "MinUddannelse - SSO", "0029": "MinUddannelse – Ugenoter"}
    selected = next((w for w in MU_OPGAVER_WIDGETS if w in widgets), None)
    assert selected == "0023"


def test_widget_selection__none_available():
    widgets = {"0029": "MinUddannelse – Ugenoter"}
    selected = next((w for w in MU_OPGAVER_WIDGETS if w in widgets), None)
    assert selected is None


def test_decode_deeplink():
    url = (
        "https://api.minuddannelse.net/aula/redirect/123456/"
        "aHR0cHMlM2ElMmYlMmZ3d3cubWludWRkYW5uZWxzZS5uZXQlMmZOb2RlJTJmbWludWdlJTJmMTIzNDU2NyUzZnVnZSUzZDIwMjYtVzMz"
    )
    assert (
        decode_mu_deeplink(url)
        == "https://www.minuddannelse.net/Node/minuge/1234567?uge=2026-W33"
    )


def test_decode_deeplink__invalid_returns_none():
    assert decode_mu_deeplink("not-a-valid-deeplink") is None
    assert decode_mu_deeplink("") is None


def test_format__matching_child(sample__opgaveliste):
    html = format_mu_opgaver(sample__opgaveliste, "Test")
    assert (
        '<h2><a href="https://www.minuddannelse.net/Node/minuge/1234567?uge=2026-W33"'
        ' target="_blank">Ugeplan 4.B</a></h2>' in html
    )
    assert "<h3>Test Testesen</h3>" in html
    assert "Ugedag: Mandag<br>" in html
    assert "Type: SimpelLektie<br>" in html
    assert "Hold: Ugeplan 4B<br>" in html
    assert "Forløb: Test Forløb" in html
    # opgaver belonging to another child are not included
    assert "Matematik afleveringsopgave" not in html


def test_format__undecodable_url_falls_back_to_plain_title(sample__opgaveliste):
    html = format_mu_opgaver(sample__opgaveliste, "Anden")
    assert "<h2>Matematik afleveringsopgave</h2>" in html
    assert "<a href=" not in html


def test_format__missing_forloeb_is_tolerated(sample__opgaveliste):
    # the second fixture entry has no "forloeb" key at all
    html = format_mu_opgaver(sample__opgaveliste, "Anden")
    assert "Forløb:" not in html


def test_format__no_opgaver_for_child(sample__opgaveliste):
    assert format_mu_opgaver(sample__opgaveliste, "Ukendt") == ""
