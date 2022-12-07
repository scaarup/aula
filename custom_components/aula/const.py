from collections import namedtuple

STARTUP = """
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
API_VERSION = "14"
MIN_UDDANNELSE_API = "https://api.minuddannelse.net/aula"
MEEBOOK_API = "https://app.meebook.com/aulaapi"
CONF_SCHOOLSCHEDULE = "schoolschedule"
CONF_UGEPLAN = "ugeplan"
