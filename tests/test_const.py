from custom_components.aula.const import (
    CONF_TEACHER_FULL_NAME,
    CONF_TEACHER_NAME_DISPLAY,
    TEACHER_NAME_INITIALS,
    TEACHER_NAME_FULL,
    resolve_teacher_name_display,
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
