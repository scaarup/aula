"""A login server outage must not be treated as a rejected refresh token.

When login.aula.dk timed out during a token renewal, the integration used to
raise ConfigEntryAuthFailed, which makes Home Assistant ask the user to sign in
with MitID again and stop polling, although the stored refresh token was fine.
"""

import asyncio
import threading
from types import SimpleNamespace

import pytest
import requests
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.aula import sensor
from custom_components.aula.aula_login_client.client import AulaLoginClient
from custom_components.aula.aula_login_client.exceptions import NetworkError
from custom_components.aula.client import Client
from custom_components.aula.const import CONF_MITID_USERNAME, DOMAIN

TOKENS = {
    "access_token": "access-1",
    "refresh_token": "refresh-1",
    "expires_in": 3600,
    "expires_at": 1000,
}


# AulaLoginClient.renew_access_token


class FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


def make_login_client(post):
    client = object.__new__(AulaLoginClient)
    client.tokens = dict(TOKENS)
    client.auth_base_url = "https://login.aula.dk"
    client.client_id_level_3 = "client-id"
    client.timeout = 30
    client.session = SimpleNamespace(headers={"User-Agent": "test"}, post=post)
    client.log = lambda *args, **kwargs: None
    return client


def raising(exc):
    def post(*args, **kwargs):
        raise exc

    return post


@pytest.mark.parametrize(
    "exc",
    [
        requests.exceptions.ReadTimeout("Read timed out. (read timeout=30)"),
        requests.exceptions.ConnectionError("Connection refused"),
    ],
)
def test_renewal_network_error_raises(exc):
    client = make_login_client(raising(exc))

    with pytest.raises(NetworkError) as err:
        client.renew_access_token()

    assert err.value.__cause__ is exc
    assert client.tokens == TOKENS


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_renewal_server_error_raises(status):
    client = make_login_client(lambda *args, **kwargs: FakeResponse(status))

    with pytest.raises(NetworkError, match=str(status)):
        client.renew_access_token()

    assert client.tokens == TOKENS


@pytest.mark.parametrize("status", [400, 401])
def test_renewal_rejected_returns_false(status):
    response = FakeResponse(status, text='{"error":"invalid_grant"}')
    client = make_login_client(lambda *args, **kwargs: response)

    assert client.renew_access_token() is False
    assert client.tokens == TOKENS


def test_renewal_bad_payload_returns_false():
    # Not a network problem: keeps the old "renewal failed" behaviour.
    client = make_login_client(lambda *args, **kwargs: FakeResponse(200, payload={}))

    assert client.renew_access_token() is False


# Client


class TimingOutPost:
    """login.aula.dk as seen on the day: every token request times out."""

    def __init__(self):
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        raise requests.exceptions.ReadTimeout("Read timed out. (read timeout=30)")


def make_expired_login_client(post):
    login_client = make_login_client(post)
    login_client.check_token_expiration = lambda: {
        "valid": False,
        "reason": "Token expired",
        "expires_in": 0,
    }
    login_client.authentications = 0

    def authenticate():
        login_client.authentications += 1
        return {"success": False, "error": "should not be called"}

    login_client.authenticate = authenticate
    return login_client


def rejected(*args, **kwargs):
    return FakeResponse(400, text='{"error":"invalid_grant"}')


class FakeSession:
    """Aula's API answers, but rejects the expired access token."""

    def __init__(self):
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        return FakeResponse(
            200, payload={"status": {"message": "Invalid access token"}}
        )


def make_client(post):
    client = object.__new__(Client)
    client._tokens = dict(TOKENS)
    client._aula_client = make_expired_login_client(post)
    client._token_refresh_lock = threading.Lock()
    client._session = FakeSession()
    client._hass = None
    client._config_entry = None
    client.apiurl = "https://www.aula.dk/api/v25/"
    return client


def test_login_unreachable_server_is_not_auth_failure():
    client = make_client(TimingOutPost())

    with pytest.raises(ConfigEntryNotReady, match="unreachable"):
        client.login()

    assert client._tokens == TOKENS
    assert client._aula_client.authentications == 0


def test_login_rejected_refresh_token_still_needs_reauth():
    client = make_client(rejected)

    with pytest.raises(ConfigEntryAuthFailed):
        client.login()


def test_ensure_valid_token_does_not_reauthenticate_on_network_error(caplog):
    post = TimingOutPost()
    client = make_client(post)

    assert client._ensure_valid_token() is False

    # One renewal attempt, and no login() that would try again.
    assert post.calls == 1
    assert "unreachable" in caplog.text
    assert not client._token_refresh_lock.locked()


def test_update_data_during_outage_is_not_auth_failure():
    # The sequence from the logs: refresh times out, the API rejects the
    # expired token, and update_data falls back to login().
    client = make_client(TimingOutPost())

    with pytest.raises(ConfigEntryNotReady, match="unreachable"):
        client.update_data()

    assert client._session.calls == 1
    assert client._tokens == TOKENS


# Sensor coordinator


class StopSetup(Exception):
    pass


def capture_update_method(monkeypatch, update_data):
    captured = {}

    class FakeCoordinator:
        def __init__(self, hass, logger, **kwargs):
            captured.update(kwargs)

        async def async_request_refresh(self):
            raise StopSetup

    async def run_in_executor(func, *args):
        return func(*args)

    monkeypatch.setattr(sensor, "DataUpdateCoordinator", FakeCoordinator)
    entry = SimpleNamespace(entry_id="entry-1", options={})
    hass = SimpleNamespace(
        data={
            DOMAIN: {
                "entry-1": {CONF_MITID_USERNAME: "mitid-user"},
                "client": SimpleNamespace(update_data=update_data),
            }
        },
        async_add_executor_job=run_in_executor,
    )
    with pytest.raises(StopSetup):
        asyncio.run(sensor.async_setup_entry(hass, entry, lambda entities: None))
    return captured["update_method"]


def test_coordinator_turns_not_ready_into_update_failed(monkeypatch):
    def update_data():
        raise ConfigEntryNotReady("Aula login server unreachable")

    update = capture_update_method(monkeypatch, update_data)

    with pytest.raises(UpdateFailed, match="unreachable"):
        asyncio.run(update())


def test_coordinator_passes_auth_failure_through(monkeypatch):
    def update_data():
        raise ConfigEntryAuthFailed("Token expired and refresh failed")

    update = capture_update_method(monkeypatch, update_data)

    with pytest.raises(ConfigEntryAuthFailed):
        asyncio.run(update())
