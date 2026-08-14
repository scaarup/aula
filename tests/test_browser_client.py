from unittest.mock import MagicMock

from custom_components.aula.aula_login_client.mitid_browserclient.BrowserClient import (
    BrowserClient,
)

APP = {"id": "S3", "combinationItems": [{"name": "MitID app"}]}
APP_CHIP = {"id": "S4", "combinationItems": [{"name": "MitID app + chip"}]}
APP_LOW = {"id": "L2", "combinationItems": [{"name": "MitID app"}]}
KODEVISER = {"id": "S1", "combinationItems": [{"name": "MitID kodeviser"}]}
UNKNOWN = {"id": "S2", "combinationItems": [{"name": "?"}]}


def _response(status_code=200, json_data=None, content=b""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.content = content
    return resp


def _next_response(combinations, next_authenticator="APP"):
    return _response(
        200,
        {
            "errors": [],
            "nextAuthenticator": {
                "authenticatorType": next_authenticator,
                "authenticatorSessionFlowKey": "flow-key",
                "eafeHash": "eafe-hash",
                "authenticatorSessionId": "authenticator-session-1",
            },
            "combinations": combinations,
        },
    )


def _make_client():
    session = MagicMock()
    session.get.return_value = _response(
        200,
        {
            "brokerSecurityContext": "ctx",
            "serviceProviderName": "Aula",
            "referenceTextHeader": "Log on at Unilogin",
            "referenceTextBody": "Log on",
        },
    )
    client = BrowserClient("client-hash", "auth-session-1", session)
    return client, session


def _identify(session, client, combinations, next_authenticator="APP"):
    session.put.return_value = _response(200)
    session.post.return_value = _next_response(combinations, next_authenticator)
    return client.identify_as_user_and_get_available_authenticators("user-1")


class TestIdentifyAsUserAndGetAvailableAuthenticators:
    def test_skips_unknown_combination_ids_instead_of_raising(self):
        client, session = _make_client()

        available = _identify(session, client, [UNKNOWN, APP, KODEVISER])

        assert available == {"APP": "MitID app", "TOKEN": "MitID kodeviser"}

    def test_all_unknown_combination_ids_yields_no_authenticators(self):
        client, session = _make_client()

        available = _identify(session, client, [UNKNOWN])

        assert available == {}


class TestCombinationIdFor:
    def test_falls_back_to_static_table_without_prior_identify(self):
        client, _ = _make_client()

        combination_id = client._BrowserClient__combination_id_for("APP")

        assert combination_id == "S3"

    def test_uses_the_id_the_account_was_offered(self):
        """Account carries the app only as S4; the static table alone would say S3."""
        client, session = _make_client()
        _identify(session, client, [APP_CHIP, KODEVISER], next_authenticator="TOKEN")

        assert client._BrowserClient__combination_id_for("APP") == "S4"

    def test_prefers_plain_app_over_chip_variant_when_both_offered(self):
        client, session = _make_client()
        _identify(
            session, client, [APP_CHIP, APP, APP_LOW, KODEVISER], next_authenticator="TOKEN"
        )

        assert client._BrowserClient__combination_id_for("APP") == "S3"

    def test_falls_back_to_low_assurance_app(self):
        client, session = _make_client()
        _identify(session, client, [APP_LOW, KODEVISER], next_authenticator="TOKEN")

        assert client._BrowserClient__combination_id_for("APP") == "L2"

    def test_raises_when_account_was_not_offered_the_authenticator(self):
        client, session = _make_client()
        _identify(session, client, [KODEVISER], next_authenticator="TOKEN")

        try:
            client._BrowserClient__combination_id_for("APP")
            assert False, "expected an exception"
        except Exception as e:
            assert "APP authentication is not available" in str(e)

    def test_rejects_a_name_no_authenticator_maps_to(self):
        client, _ = _make_client()

        try:
            client._BrowserClient__combination_id_for("FACE_ID")
            assert False, "expected an exception"
        except Exception as e:
            assert "No such authenticator name" in str(e)


class TestSelectAuthenticatorPostsOfferedId:
    def test_posts_the_offered_combination_id(self):
        """Posting a hardcoded S3 would be rejected by MitID for an S4-only account."""
        client, session = _make_client()
        _identify(session, client, [APP_CHIP, KODEVISER], next_authenticator="TOKEN")

        session.post.return_value = _next_response([APP_CHIP, KODEVISER], next_authenticator="APP")
        client._BrowserClient__select_authenticator("APP")

        assert session.post.call_args.kwargs["json"] == {"combinationId": "S4"}
