"""Extract the guardian identifier used by Aula widget requests."""
import base64
import json


def widget_guardian(token):
    """Read the widget token's subject for sessionUUID (not token verification)."""
    try:
        payload = token.removeprefix("Bearer ").split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        subject = claims.get("sub")
    except (AttributeError, IndexError, ValueError, TypeError) as err:
        raise ValueError("Invalid widget token") from err
    if not isinstance(subject, str) or not subject:
        raise ValueError("Widget token has no guardian subject")
    return subject
