"""Keep credentials out of the integration's log records.

Aula takes the access token as a URL query parameter, and ``requests`` puts the
URL in the text of its exceptions. So a failed request can put a working token
in a warning. The login flow also logs URLs, form data and response headers at
debug level.

The log calls across the integration are already formatted strings, so the
filter scrubs the finished message and the traceback, not the arguments.

This is a safety net, not a guarantee. It recognises the patterns below. A
secret logged without a name next to it, such as free text, is not caught.
"""

import logging
import re
import traceback

REDACTED = "[redacted]"

# Aula access tokens and the widget bearer tokens are JWTs.
_JWT = re.compile(r"eyJ[\w-]*\.[\w-]+\.[\w-]*")

# Names whose value is a credential or a session secret wherever it shows up.
_SECRET_NAMES = (
    "access_token",
    "refresh_token",
    "id_token",
    "code_verifier",
    "session_code",
    "samlresponse",
    "samlrequest",
    "relaystate",
    "__requestverificationtoken",
    "mitidauthcode",
    "csrfp-token",
    "authorization",
    "aula-authorization",
    "set-cookie",
    "cookie",
    "password",
)

# Names that are too common to match on their own. They are only treated as
# secret in the "name=value" form of a URL, a form body or an HTML attribute.
# "value" covers hidden form inputs in logged HTML.
_PARAMETER_NAMES = ("code", "state", "token", "execution", "tab_id", "value")

# Names the login flow logs as "name: value" without quotes.
_LABEL_NAMES = (
    "access_token",
    "refresh_token",
    "id_token",
    "session_code",
    "execution",
    "tab_id",
    "password",
)


def _alternatives(names):
    return "|".join(re.escape(name) for name in names)


# name=value. The lookbehind keeps "status_code=403" readable.
_PARAMETER = re.compile(
    r"(?i)(?<![\w-])("
    + _alternatives(_SECRET_NAMES + _PARAMETER_NAMES)
    + r""")=("[^"]*"|'[^']*'|[^\s&,;"'<>)]+)"""
)

# "name": "value" in a dict or JSON dump. Only string values are replaced, so
# the numeric status codes in Aula's replies stay readable.
_FIELD = re.compile(
    r"(?i)([\"'])("
    + _alternatives(_SECRET_NAMES + _PARAMETER_NAMES)
    + r""")\1\s*:\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')"""
)

# name: value
_LABEL = re.compile(
    r"(?i)(?<![\w\"'-])(" + _alternatives(_LABEL_NAMES) + r"""):\s*([^\s,;"']+)"""
)


def _redact_field(match):
    quote = match.group(1)
    return f"{quote}{match.group(2)}{quote}: {quote}{REDACTED}{quote}"


def redact(text):
    """Return ``text`` with recognised credentials replaced by a marker."""
    text = str(text)
    text = _FIELD.sub(_redact_field, text)
    text = _PARAMETER.sub(rf"\1={REDACTED}", text)
    text = _LABEL.sub(rf"\1: {REDACTED}", text)
    return _JWT.sub(REDACTED, text)


class RedactingFilter(logging.Filter):
    """Scrub the message and the traceback of every record it sees."""

    def filter(self, record):
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a bad format string is logging's to report
            message = None
        if message is not None:
            record.msg = redact(message)
            record.args = None

        if record.exc_info and record.exc_info[0] is not None:
            # The exception text holds the request URL. Keep the traceback as
            # scrubbed text and drop the exception, so no handler formats it again.
            formatted = "".join(traceback.format_exception(*record.exc_info))
            record.exc_text = redact(formatted.rstrip("\n"))
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = redact(record.exc_text)

        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True


def redacting_logger(name):
    """Return the logger ``name`` with the redacting filter attached.

    A filter on a logger only sees records logged through that logger, not the
    ones from its children. So every module builds its logger here.
    """
    logger = logging.getLogger(name)
    if not any(isinstance(item, RedactingFilter) for item in logger.filters):
        logger.addFilter(RedactingFilter())
    return logger
