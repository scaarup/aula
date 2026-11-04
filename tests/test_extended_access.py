from unittest.mock import MagicMock

from custom_components.aula.client import Client

OWN = {
    "id": 1,
    "userId": 11,
    "name": "Own Child",
    "institutionCode": "A1",
    "institutionProfile": {"institutionName": "School A"},
}
EXTRA = {"id": 2, "userId": 22, "name": "Bonus Daughter", "institutionCode": "B2"}


def _client(context_json):
    client = Client.__new__(Client)
    client.apiurl = "https://x/?"
    client._ensure_valid_token = lambda: None
    client._get_access_token_param = lambda: ""
    client._profiles = [
        {"children": [OWN], "institutionProfiles": [{"institutionCode": "A1"}]}
    ]
    client.presence = {}

    def get(url, **kwargs):
        resp = MagicMock()
        if "getProfileContext" in url:
            if isinstance(context_json, Exception):
                raise context_json
            resp.json.return_value = context_json
        elif "getProfilesByLogin" in url:
            resp.json.return_value = {"status": {"message": "OK"}}
        else:
            # Stop after the child lists are built; the rest is not under test.
            raise RuntimeError("stop")
        return resp

    client._session = MagicMock()
    client._session.get.side_effect = get
    return client


def _run(client):
    try:
        client.update_data()
    except RuntimeError as err:
        assert str(err) == "stop"


def test_extended_access_child_is_added():
    ctx = {"data": {"institutions": [{"name": "School B", "children": [EXTRA, dict(OWN)]}]}}
    client = _client(ctx)
    _run(client)
    assert client._childids == ["1", "2"]
    assert client._childuserids == ["11", "22"]
    assert client._institutions[2] == "School B"
    assert client._childrenFirstNamesAndUserIDs[22] == "Bonus"
    assert client._institutionProfiles == ["A1", "B2"]
    assert client._children[1]["institutionProfile"] == {"institutionName": "School B"}


def test_context_failure_keeps_own_children():
    client = _client(RuntimeError("boom"))
    # RuntimeError("boom") is swallowed by the warning path in update_data
    _run(client)
    assert client._childids == ["1"]


def test_empty_context():
    client = _client({"data": None})
    _run(client)
    assert client._childids == ["1"]
