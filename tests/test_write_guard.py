"""aula.api_call is read-only unless writing is allowed."""

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.aula.write_guard import check_api_call


def test_get_is_always_allowed():
    assert check_api_call({}, None) is False
    assert check_api_call({}, "") is False


def test_post_is_refused_by_default():
    with pytest.raises(ServiceValidationError):
        check_api_call({}, '{"a": 1}')
    with pytest.raises(ServiceValidationError):
        check_api_call({"allow_api_write": False}, '{"a": 1}')


def test_post_is_allowed_when_switched_on():
    assert check_api_call({"allow_api_write": True}, '{"a": 1}') is True
