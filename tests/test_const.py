from custom_components.aula.const import (
    CONF_TEACHER_FULL_NAME,
    CONF_TEACHER_NAME_DISPLAY,
    TEACHER_NAME_INITIALS,
    TEACHER_NAME_FULL,
    resolve_teacher_name_display,
    DEFAULT_SUBJECT_EMOJI,
    get_subject_emoji,
)


def test_resolve_teacher_name_display__new_key_wins():
    data = {CONF_TEACHER_NAME_DISPLAY: TEACHER_NAME_FULL, CONF_TEACHER_FULL_NAME: False}
    assert resolve_teacher_name_display(data) == TEACHER_NAME_FULL


def test_resolve_teacher_name_display__migrates_old_true():
    assert resolve_teacher_name_display({CONF_TEACHER_FULL_NAME: True}) == TEACHER_NAME_FULL


def test_resolve_teacher_name_display__migrates_old_false():
    assert resolve_teacher_name_display({CONF_TEACHER_FULL_NAME: False}) == TEACHER_NAME_INITIALS


def test_resolve_teacher_name_display__no_data_defaults_to_initials():
    assert resolve_teacher_name_display({}) == TEACHER_NAME_INITIALS


def test_get_subject_emoji__exact_match():
    assert get_subject_emoji("Dansk") == "📖"


def test_get_subject_emoji__substring_with_suffix():
    assert get_subject_emoji("Håndværk og design (2-vok)") == "🔨"


def test_get_subject_emoji__substring_with_prefix():
    assert get_subject_emoji("Valgfag Madkundskab (Prøvefag)") == "🍳"


def test_get_subject_emoji__unmatched_falls_back_to_default():
    assert get_subject_emoji("Robotteknologi") == DEFAULT_SUBJECT_EMOJI
