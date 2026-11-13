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
    assert html.startswith("<p>K&aelig;re")
    assert text == "Kære alle forældre\nMatematik:\nVi glæder os.\nOnsdag\nFremlægger:\nA, B og C"


def test_weekly_letter_empty_or_hidden():
    assert parse_easyiq_weekly_letter({"Show": False, "Text": "", "IsVisible": False, "WeekPlans": []}) == (None, None)
    assert parse_easyiq_weekly_letter({"WeekPlans": [{"Text": "<p>x</p>", "IsVisible": "false"}]}) == (None, None)
    assert parse_easyiq_weekly_letter(None) == (None, None)


def test_weekly_letter_top_level_and_class_texts():
    _, text = parse_easyiq_weekly_letter(
        {"Show": True, "IsVisible": True, "Text": "<p>Top</p>", "WeekPlans": [{"Text": "<p>Klasse</p>", "IsVisible": True}]}
    )
    assert text == "Top\n\nKlasse"
