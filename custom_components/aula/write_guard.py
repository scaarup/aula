"""Keep aula.api_call read-only unless writing is allowed.

aula.api_call can send any request to Aula as the logged-in guardian. With
post_data it can write: answer messages, change pick-up times, mark threads
as read. Anything holding a Home Assistant token (scripts, other add-ons,
an AI agent) can call it, so POST is refused unless "allow_api_write" is
switched on under Configure.
"""

from __future__ import annotations

from homeassistant.exceptions import ServiceValidationError

from .const import CONF_ALLOW_API_WRITE

REFUSED = (
    "aula.api_call is read-only: post_data (writing to Aula) is turned off. "
    "Turn on 'Allow writing to Aula via aula.api_call' under Configure to allow it."
)


def check_api_call(entry_data, post_data) -> bool:
    """Return True for a write call that is allowed, False for a read call.

    Raises ServiceValidationError for a write call while writing is off.
    """
    if not post_data:
        return False
    if not (entry_data or {}).get(CONF_ALLOW_API_WRITE, False):
        raise ServiceValidationError(REFUSED)
    return True
