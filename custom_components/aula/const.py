from collections import namedtuple

STARTUP = r"""
                _
     /\        | |
    /  \  _   _| | __ _
   / /\ \| | | | |/ _` |
  / ____ \ |_| | | (_| |
 /_/    \_\__,_|_|\__,_|
Aula integration, version: %s
This is a custom integration
If you have any issues with this you need to open an issue here:
https://github.com/scaarup/aula/issues
-------------------------------------------------------------------
"""

DOMAIN = "aula"
API = "https://www.aula.dk/api/v"
API_VERSION = "22"
MIN_UDDANNELSE_API = "https://api.minuddannelse.net/aula"
MEEBOOK_API = "https://app.meebook.com/aulaapi"
SYSTEMATIC_API = "https://systematic-momo.dk/api/aula"
EASYIQ_API = "https://api.easyiqcloud.dk/api/aula"
CONF_SCHOOLSCHEDULE = "schoolschedule"
CONF_UGEPLAN = "ugeplan"
CONF_MU_OPGAVER = "mu_opgaver"

# Authentication method constants
CONF_MITID_USERNAME = "mitid_username"
CONF_MITID_PASSWORD = "mitid_password"  # Optional, for TOKEN method
CONF_AUTH_METHOD = "auth_method"
CONF_MITID_IDENTITY = "mitid_identity"  # Optional, for multiple identity selection (1-based index)
AUTH_METHOD_APP = "APP"
AUTH_METHOD_TOKEN = "TOKEN"

# Token storage keys
CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_TOKEN_EXPIRES_AT = "token_expires_at"
