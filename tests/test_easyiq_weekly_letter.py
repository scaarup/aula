from custom_components.aula.client import parse_easyiq_weekly_letter


WEEKLY_LETTER = {
    "Show": False,
    "TextId": "",
    "Text": "",
    "IsVisible": False,
    "Beskrivelse": "Generelt om ugen",
    "WeekPlans": [
        {
            "ActivityName": "2B",
            "TextId": "00000000-0000-0000-0000-000000000000",
            "IsVisible": True,
            "Beskrivelse": "Generelt om ugen",
            "Text": (
                "<p>K&aelig;re alle for&aelig;ldre</p><p><strong>Matematik:</strong></p>"
                "<p>Vi gl&aelig;der os.</p><p>&nbsp;</p><p>Onsdag <em></em></p>"
                "<p><em>Freml&aelig;gger:</em><br /><em>A, B og C</em></p>"
            ),
        }
    ],
}


def test_weekly_letter_html_and_plain_text():
    html, text = parse_easyiq_weekly_letter(WEEKLY_LETTER)
    assert html.startswith("<p>Kære")
    assert text == "Kære alle forældre\nMatematik:\nVi glæder os.\nOnsdag\nFremlægger:\nA, B og C"


def test_weekly_letter_sanitizes_html_and_strips_bloat():
    dirty = {
        "WeekPlans": [
            {
                "IsVisible": True,
                "Text": (
                    '<p class="MsoNormal" style="font-family: Calibri; color: red;" onclick="x()">'
                    '<font size="3">Husk <strong>madpakke</strong></font>'
                    '<script>alert(1)</script> '
                    '<a href="javascript:alert(1)">bad</a> '
                    '<a href="https://example.com" target="_blank" data-id="1">link</a>'
                    "</p>"
                ),
            }
        ]
    }
    html, text = parse_easyiq_weekly_letter(dirty)
    assert "style=" not in html
    assert "class=" not in html
    assert "onclick=" not in html
    assert "<font" not in html
    assert "script" not in html
    assert "javascript:" not in html
    assert "target=" not in html
    assert "data-id=" not in html
    assert "<p>Husk <strong>madpakke</strong>" in html
    assert '<a href="https://example.com">link</a>' in html
    assert text == "Husk madpakke bad link"


def test_weekly_letter_only_disallowed_html_returns_none():
    assert parse_easyiq_weekly_letter({"IsVisible": True, "Text": "<script>alert(1)</script>"}) == (None, None)


def test_weekly_letter_empty_or_hidden():
    assert parse_easyiq_weekly_letter({"Show": False, "Text": "", "IsVisible": False, "WeekPlans": []}) == (None, None)
    assert parse_easyiq_weekly_letter({"WeekPlans": [{"Text": "<p>x</p>", "IsVisible": "false"}]}) == (None, None)
    assert parse_easyiq_weekly_letter(None) == (None, None)


def test_weekly_letter_top_level_and_class_texts():
    _, text = parse_easyiq_weekly_letter(
        {"Show": True, "IsVisible": True, "Text": "<p>Top</p>", "WeekPlans": [{"Text": "<p>Klasse</p>", "IsVisible": True}]}
    )
    assert text == "Top\n\nKlasse"
