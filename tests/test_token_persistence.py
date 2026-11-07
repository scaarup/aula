import asyncio
import logging
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import custom_components.aula as aula
from custom_components.aula import (
    _settings_snapshot,
    async_update_tokens,
    options_update_listener,
)
from custom_components.aula.aula_login_client import client as login_client_module
from custom_components.aula.aula_login_client.client import AulaLoginClient
from custom_components.aula.client import Client
from custom_components.aula.const import (
    CONF_ACCESS_TOKEN,
    CONF_MITID_USERNAME,
    CONF_REFRESH_TOKEN,
    CONF_SCHOOLSCHEDULE,
    CONF_TOKEN_EXPIRES_AT,
    CONF_UGEPLAN,
    DOMAIN,
)

ENTRY_ID = "entry-1"


def make_entry(data=None, options=None):
    return SimpleNamespace(
        entry_id=ENTRY_ID,
        data={
            CONF_MITID_USERNAME: "mitid-user",
            CONF_SCHOOLSCHEDULE: True,
            CONF_UGEPLAN: True,
            CONF_ACCESS_TOKEN: "access-1",
            CONF_REFRESH_TOKEN: "refresh-1",
            CONF_TOKEN_EXPIRES_AT: 1000,
            **(data or {}),
        },
        options=options or {},
    )


class FakeConfigEntries:
    """The subset of hass.config_entries the token code touches."""

    def __init__(self, entry):
        self.entry = entry
        self.updates = []
        self.async_reload = AsyncMock()

    def async_get_entry(self, entry_id):
        if self.entry is not None and self.entry.entry_id == entry_id:
            return self.entry
        return None

    def async_update_entry(self, entry, data):
        self.updates.append(data)
        entry.data = data


def make_hass(entry, snapshot=None):
    hass_data = {DOMAIN: {}}
    if snapshot is not None:
        hass_data[DOMAIN][ENTRY_ID] = {"settings_snapshot": snapshot}
    return SimpleNamespace(data=hass_data, config_entries=FakeConfigEntries(entry))


def renewed(access="access-2", refresh="refresh-2", expires_at=2000):
    tokens = {"access_token": access, "refresh_token": refresh}
    if expires_at is not None:
        tokens["expires_at"] = expires_at
    return tokens


# _settings_snapshot


def test_snapshot_ignores_token_keys():
    before = make_entry()
    after = make_entry(
        {
            CONF_ACCESS_TOKEN: "access-2",
            CONF_REFRESH_TOKEN: "refresh-2",
            CONF_TOKEN_EXPIRES_AT: 2000,
        }
    )
    assert _settings_snapshot(before) == _settings_snapshot(after)


def test_snapshot_sees_settings_changes():
    assert _settings_snapshot(make_entry()) != _settings_snapshot(
        make_entry({CONF_UGEPLAN: False})
    )


def test_snapshot_sees_options_changes():
    assert _settings_snapshot(make_entry()) != _settings_snapshot(
        make_entry(options={"some_option": True})
    )


def test_snapshot_does_not_alias_the_entry():
    entry = make_entry(options={"some_option": True})
    snapshot = _settings_snapshot(entry)
    entry.data[CONF_UGEPLAN] = False
    entry.options["some_option"] = False
    assert snapshot["data"][CONF_UGEPLAN] is True
    assert snapshot["options"]["some_option"] is True


# options_update_listener


def test_listener_skips_token_only_update():
    entry = make_entry()
    hass = make_hass(entry, snapshot=_settings_snapshot(entry))
    entry.data = {**entry.data, CONF_REFRESH_TOKEN: "refresh-2"}

    asyncio.run(options_update_listener(hass, entry))

    hass.config_entries.async_reload.assert_not_awaited()


def test_listener_reloads_on_settings_change():
    entry = make_entry()
    hass = make_hass(entry, snapshot=_settings_snapshot(entry))
    entry.data = {**entry.data, CONF_UGEPLAN: False}

    asyncio.run(options_update_listener(hass, entry))

    hass.config_entries.async_reload.assert_awaited_once_with(ENTRY_ID)


def test_listener_reloads_without_snapshot():
    # Fails open: an entry that was never set up (or already unloaded) reloads.
    entry = make_entry()
    hass = make_hass(entry)

    asyncio.run(options_update_listener(hass, entry))

    hass.config_entries.async_reload.assert_awaited_once_with(ENTRY_ID)


# async_update_tokens


def test_update_tokens_persists_rotated_tokens():
    entry = make_entry()
    hass = make_hass(entry)

    asyncio.run(async_update_tokens(hass, entry, renewed()))

    assert entry.data[CONF_ACCESS_TOKEN] == "access-2"
    assert entry.data[CONF_REFRESH_TOKEN] == "refresh-2"
    assert entry.data[CONF_TOKEN_EXPIRES_AT] == 2000
    # Settings are carried over untouched.
    assert entry.data[CONF_MITID_USERNAME] == "mitid-user"
    assert entry.data[CONF_UGEPLAN] is True


def test_update_tokens_without_expiry_stores_zero():
    entry = make_entry()
    hass = make_hass(entry)

    asyncio.run(async_update_tokens(hass, entry, renewed(expires_at=None)))

    assert entry.data[CONF_TOKEN_EXPIRES_AT] == 0


@pytest.mark.parametrize(
    "tokens",
    [
        {},
        {"access_token": "access-2"},
        {"refresh_token": "refresh-2"},
        {"access_token": "", "refresh_token": "refresh-2"},
    ],
)
def test_update_tokens_ignores_incomplete_set(tokens):
    entry = make_entry()
    hass = make_hass(entry)

    asyncio.run(async_update_tokens(hass, entry, tokens))

    assert hass.config_entries.updates == []
    assert entry.data[CONF_REFRESH_TOKEN] == "refresh-1"


def test_update_tokens_skips_removed_entry():
    entry = make_entry()
    hass = make_hass(None)

    asyncio.run(async_update_tokens(hass, entry, renewed()))

    assert hass.config_entries.updates == []


def test_persisting_tokens_does_not_reload():
    # The full cycle: the snapshot taken at setup, a renewal persisted to
    # entry.data, then the update listener HA fires for that write.
    entry = make_entry()
    hass = make_hass(entry, snapshot=_settings_snapshot(entry))

    async def renew():
        await async_update_tokens(hass, entry, renewed())
        await options_update_listener(hass, entry)

    asyncio.run(renew())

    assert entry.data[CONF_REFRESH_TOKEN] == "refresh-2"
    hass.config_entries.async_reload.assert_not_awaited()


# Client._persist_tokens


@pytest.fixture
def running_loop():
    # _persist_tokens runs in an executor thread and hops onto the event loop.
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    yield loop
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=5)
    loop.close()


def make_client(hass=None, config_entry=None, tokens=None):
    # Mirrors the persistence state set up in Client.__init__.
    client = object.__new__(Client)
    client._hass = hass
    client._config_entry = config_entry
    client._tokens = tokens if tokens is not None else renewed()
    return client


def test_persist_tokens_passes_a_copy(monkeypatch, running_loop):
    calls = []

    async def fake_update(hass, entry, tokens):
        calls.append((hass, entry, tokens))

    monkeypatch.setattr(aula, "async_update_tokens", fake_update)
    hass = SimpleNamespace(loop=running_loop)
    entry = make_entry()
    client = make_client(hass, entry)

    client._persist_tokens()

    assert len(calls) == 1
    assert calls[0][:2] == (hass, entry)
    assert calls[0][2] == client._tokens
    assert calls[0][2] is not client._tokens


def test_persist_tokens_without_hass_is_a_noop(monkeypatch):
    update = MagicMock()
    monkeypatch.setattr(aula, "async_update_tokens", update)

    make_client()._persist_tokens()
    make_client(hass=SimpleNamespace(loop=None))._persist_tokens()

    update.assert_not_called()


def test_persist_tokens_failure_is_logged_not_raised(monkeypatch, running_loop, caplog):
    async def failing_update(hass, entry, tokens):
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(aula, "async_update_tokens", failing_update)
    client = make_client(SimpleNamespace(loop=running_loop), make_entry())

    with caplog.at_level(logging.WARNING):
        client._persist_tokens()

    assert "Failed to persist renewed Aula tokens" in caplog.text


# AulaLoginClient.renew_access_token


class FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def make_login_client(payload):
    client = object.__new__(AulaLoginClient)
    client.tokens = {
        "access_token": "access-1",
        "refresh_token": "refresh-1",
        "expires_in": 3600,
        "expires_at": 1000,
    }
    client.auth_base_url = "https://login.aula.dk"
    client.client_id_level_3 = "client-id"
    client.timeout = 30
    client.session = SimpleNamespace(
        headers={"User-Agent": "test"},
        post=lambda *args, **kwargs: FakeResponse(payload),
    )
    client.log = lambda *args, **kwargs: None
    return client


def test_renewal_refreshes_expires_at(monkeypatch):
    monkeypatch.setattr(login_client_module, "time", SimpleNamespace(time=lambda: 50_000))
    client = make_login_client(
        {"access_token": "access-2", "refresh_token": "refresh-2", "expires_in": 3600}
    )

    assert client.renew_access_token() is True

    assert client.tokens["refresh_token"] == "refresh-2"
    assert client.tokens["expires_at"] == 53_600


def test_renewal_without_expires_in_keeps_expires_at(monkeypatch):
    monkeypatch.setattr(login_client_module, "time", SimpleNamespace(time=lambda: 50_000))
    client = make_login_client({"access_token": "access-2"})

    assert client.renew_access_token() is True

    assert client.tokens["refresh_token"] == "refresh-1"
    assert client.tokens["expires_at"] == 1000
