"""Credentials must not reach the log.

The bug report template asks users to post a debug log. Before this, that log
could hold the MitID password of a code-token user, and a failed request put
the access token in a warning without any debug logging.
"""

import base64
import importlib
import logging
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from homeassistant.exceptions import ConfigEntryNotReady

import custom_components.aula as aula
from custom_components.aula.aula_login_client import client as login_client_module
from custom_components.aula.aula_login_client.client import AulaLoginClient
from custom_components.aula.client import Client
from custom_components.aula.log_redaction import (
    REDACTED,
    RedactingFilter,
    redact,
    redacting_logger,
)

# Shaped like a JWT, as Aula's access tokens and the widget tokens are.
JWT = "eyJhbGciOiJub25lIn0.eyJzdWIiOiJtYXJrZXIifQ.c2lnbmF0dXJlLW1hcmtlcg"

PACKAGE = Path(aula.__file__).parent


def has_filter(logger):
    return any(isinstance(item, RedactingFilter) for item in logger.filters)


# redact()


def test_failed_request_keeps_the_method_and_drops_the_token():
    text = (
        "HTTPSConnectionPool(host='www.aula.dk', port=443): Max retries exceeded "
        f"with url: /api/v25?method=profiles.getProfilesByLogin&access_token={JWT} "
        "(Caused by NameResolutionError('Failed to resolve www.aula.dk'))"
    )

    cleaned = redact(text)

    assert JWT not in cleaned
    assert f"access_token={REDACTED}" in cleaned
    assert "method=profiles.getProfilesByLogin" in cleaned
    assert "(Caused by NameResolutionError" in cleaned


def test_token_that_is_not_a_jwt_is_dropped_by_its_name():
    text = (
        "403 Client Error: Forbidden for url: https://www.aula.dk/api/v25"
        "?method=profiles.getContactlist&groupId=12&access_token=opaque-marker&page=1"
    )

    cleaned = redact(text)

    assert "opaque-marker" not in cleaned
    assert "groupId=12" in cleaned
    assert "page=1" in cleaned


@pytest.mark.parametrize(
    ("text", "kept"),
    [
        (
            "Found OAuth callback URL: https://app-private.aula.dk?code=a-marker&state=b-marker",
            "https://app-private.aula.dk?code=",
        ),
        (
            "Broker params - session_code: a-marker, execution: b-marker, "
            "client_id: unilogin, tab_id: c-marker",
            "client_id: unilogin",
        ),
        (
            "Post-broker form data: {'SAMLResponse': 'a-marker', 'RelayState': 'b-marker', "
            "'selected-aktoer': 'KONTAKT'}",
            "'selected-aktoer': 'KONTAKT'",
        ),
        (
            "Post-broker response headers: {'Set-Cookie': 'AUTH_SESSION_ID=a-marker; Path=/', "
            "'Content-Type': 'text/html'}",
            "'Content-Type': 'text/html'",
        ),
        (
            f"EasyIQ legacy headers {{'Authorization': 'Bearer {JWT}', "
            "'csrfp-token': 'a-marker', 'accept': 'application/json'}",
            "'accept': 'application/json'",
        ),
        (
            '<input type="hidden" name="SAMLResponse" value="a-marker"/>',
            'name="SAMLResponse"',
        ),
        (
            '<form action="https://broker.unilogin.dk/login?session_code=a-marker'
            '&amp;execution=b-marker&amp;client_id=unilogin&amp;tab_id=c-marker">',
            "client_id=unilogin",
        ),
        (
            "  Form input: name=session, type=hidden, value=a-marker",
            "name=session, type=hidden",
        ),
        (
            '{"access_token": "a-marker", "refresh_token": "b-marker", "expires_in": 3600}',
            '"expires_in": 3600',
        ),
    ],
)
def test_login_flow_secrets_are_dropped(text, kept):
    cleaned = redact(text)

    assert "marker" not in cleaned
    assert JWT not in cleaned
    assert kept in cleaned


@pytest.mark.parametrize(
    "text",
    [
        "Unexpected API response: 403",
        '{"status": {"code": 10, "subCode": 23, "message": "Session expired"}}',
        "MU Opgaver status_code=200",
        "response_type=code&code_challenge=abc&code_challenge_method=S256",
        "Token expires in 1h 5m (3900s)",
        "Trying API at https://www.aula.dk/api/v25",
    ],
)
def test_text_without_secrets_is_left_alone(text):
    assert redact(text) == text


def test_redacting_twice_changes_nothing():
    text = f"url: /api/v25?method=a.b&access_token={JWT} {{'Cookie': 'x=y'}} state=abc"

    assert redact(redact(text)) == redact(text)


# The filter


def test_preformatted_message_is_scrubbed(caplog):
    logger = redacting_logger("custom_components.aula.tests.preformatted")

    with caplog.at_level(logging.DEBUG, logger=logger.name):
        logger.error(f"API verification error: url: /api/v25?access_token={JWT}")

    assert JWT not in caplog.text
    assert "API verification error" in caplog.text


def test_exception_argument_is_scrubbed(caplog):
    logger = redacting_logger("custom_components.aula.tests.argument")
    error = requests.HTTPError(
        "403 Client Error: Forbidden for url: https://www.aula.dk/api/v25"
        f"?method=groups.getGroupsByContext&access_token={JWT}"
    )

    with caplog.at_level(logging.DEBUG, logger=logger.name):
        logger.warning("Unable to retrieve Aula child groups: %s", error)

    assert JWT not in caplog.text
    assert "method=groups.getGroupsByContext" in caplog.text


def test_traceback_is_scrubbed(caplog):
    # Home Assistant's update coordinator logs through the logger it is given.
    logger = redacting_logger("custom_components.aula.tests.traceback")

    with caplog.at_level(logging.DEBUG, logger=logger.name):
        try:
            raise requests.ConnectionError(f"url: /api/v25?access_token={JWT}")
        except requests.ConnectionError:
            logger.exception("Unexpected error fetching sensor data")

    assert JWT not in caplog.text
    assert "Traceback" in caplog.text
    assert "ConnectionError" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_filter_is_attached_once():
    logger = redacting_logger("custom_components.aula.tests.once")
    redacting_logger("custom_components.aula.tests.once")

    assert sum(isinstance(item, RedactingFilter) for item in logger.filters) == 1


# Every module is behind the filter


@pytest.mark.parametrize(
    "suffix",
    [
        "",
        ".binary_sensor",
        ".calendar",
        ".client",
        ".config_flow",
        ".sensor",
        ".views",
        ".aula_login_client.client",
        ".aula_login_client.mitid_browserclient.BrowserClient",
        ".aula_login_client.mitid_browserclient.Helpers",
    ],
)
def test_module_logger_has_the_filter(suffix):
    name = "custom_components.aula" + suffix
    importlib.import_module(name)
    if suffix == ".aula_login_client.client":
        # This one builds its logger when a client is created.
        assert has_filter(AulaLoginClient("mitid-user").logger)

    assert has_filter(logging.getLogger(name))


def test_no_module_builds_a_plain_logger():
    # A command-line script from the MitID client. The integration never imports it.
    standalone = PACKAGE / "aula_login_client/mitid_browserclient/login_flows/aula.py"

    plain = [
        str(path.relative_to(PACKAGE))
        for path in sorted(PACKAGE.rglob("*.py"))
        if path.name != "log_redaction.py"
        and path != standalone
        and "logging.getLogger(" in path.read_text(encoding="utf-8")
    ]

    assert plain == []


# The two leaks this was written for


def test_code_token_login_does_not_log_the_password_or_the_code(monkeypatch, caplog):
    class FakeMitID:
        def __init__(self, *args):
            pass

        def identify_as_user_and_get_available_authenticators(self, user_id):
            return {"TOKEN": "MitID kodeviser"}

        def authenticate_with_token(self, digits):
            pass

        def authenticate_with_password(self, password):
            pass

        def finalize_authentication_and_get_authorization_code(self):
            return "authorization-code"

    monkeypatch.setattr(login_client_module, "BrowserClient", FakeMitID)
    # verbose and debug are what the config flow passes.
    client = AulaLoginClient(
        "mitid-user",
        mitid_password="password-marker",
        mitid_token="424242",
        auth_method="TOKEN",
        verbose=True,
        debug=True,
    )
    aux = {
        "coreClient": {"checksum": base64.b64encode(b"checksum").decode()},
        "parameters": {"authenticationSessionId": "session-1"},
    }

    with caplog.at_level(logging.DEBUG):
        assert client._get_mitid_authentication_code(aux) == "authorization-code"

    assert "Starting token authentication" in caplog.text
    assert "password-marker" not in caplog.text
    assert "424242" not in caplog.text


class UnreachableSession:
    """www.aula.dk cannot be resolved. requests puts the URL in the error text."""

    def __init__(self):
        self.headers = {}

    def get(self, url, **kwargs):
        path = url.split("www.aula.dk", 1)[1]
        raise requests.ConnectionError(
            "HTTPSConnectionPool(host='www.aula.dk', port=443): Max retries exceeded "
            f"with url: {path} (Caused by NameResolutionError('Failed to resolve'))"
        )


def test_unreachable_api_keeps_the_token_out_of_the_log_and_the_error(caplog):
    tokens = {"access_token": JWT, "refresh_token": "refresh-1"}
    client = object.__new__(Client)
    client._tokens = dict(tokens)
    client._aula_client = SimpleNamespace(
        tokens=dict(tokens),
        check_token_expiration=lambda: {"valid": True, "expires_in": 3600},
        renew_access_token=lambda: True,
    )
    client._token_refresh_lock = threading.Lock()
    client._session = UnreachableSession()
    client._hass = None
    client._config_entry = None

    with caplog.at_level(logging.DEBUG), pytest.raises(ConfigEntryNotReady) as error:
        client.login()

    # Home Assistant shows this text in the UI as the reason for the retry.
    assert JWT not in str(error.value)
    assert "method=profiles.getProfilesByLogin" in str(error.value)
    # The request did fail with the token in its text, and that was logged.
    assert "API verification error" in caplog.text
    assert "Stored token rejected by API" in caplog.text
    assert JWT not in caplog.text

    # Home Assistant then logs the whole exception chain at debug level, through
    # the integration's own logger. The chain holds the original request error.
    with caplog.at_level(logging.DEBUG):
        logging.getLogger("custom_components.aula").debug(
            "Full exception", exc_info=error.value
        )

    assert "During handling of the above exception" in caplog.text
    assert JWT not in caplog.text
