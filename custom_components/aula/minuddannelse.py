import logging
import time

from bs4 import BeautifulSoup
from .widget_auth import widget_guardian

_LOGGER = logging.getLogger(__name__)
from .const import (
    MIN_UDDANNELSE_API,
)


class MinUddannelse:
    def __init__(
        self,
        minUddannelseForloeb,
        minUddannelseOpgaveListe,
        minUddannelseUgeNote,
    ):
        self._minUddannelseForloeb = minUddannelseForloeb
        self._minUddannelseOpgaveListe = minUddannelseOpgaveListe
        self._minUddannelseugenote = minUddannelseUgeNote

    def forloeb(
        self, session, token, week, childuserids, institutionProfiles, username
    ):
        # Use the guardian subject from the current widget token, as Aula does.
        response = session.get(
            MIN_UDDANNELSE_API + "/forloeb",
            params={
                "assuranceLevel": 2,
                "childFilter": ",".join(childuserids),
                "currentWeekNumber": week,
                "institutionFilter": ",".join(institutionProfiles),
                "isMobileApp": "false",
                "placement": "full",
                "sessionUUID": widget_guardian(token),
                "userProfile": "guardian",
            },
            headers={"Authorization": token, "accept": "application/json"},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("personer"), list):
            raise ValueError("Missing MinUddannelse persons list")
        result = {}
        for child in payload["personer"]:
            courses = result.setdefault(child["navn"], [])
            for institution in child.get("institutioner") or []:
                for course in institution.get("forloeb") or []:
                    entry = {"Name": course["navn"], "Url": course.get("url") or "", "Description": ""}
                    # Preserve course descriptions from the existing integration.
                    # Missing/failed detail pages must not hide the course title.
                    if entry["Url"]:
                        try:
                            detail = session.post(
                                entry["Url"],
                                data={
                                    "childFilter": ",".join(childuserids),
                                    "currentWeekNumber": week,
                                    "group": "",
                                    "assuranceLevel": "2",
                                    "institutionFilter": ",".join(institutionProfiles),
                                    "isMobileApp": "false",
                                    "placement": "narrow",
                                    "sessionUUID": widget_guardian(token),
                                    "userProfile": "guardian",
                                },
                                headers={"Authorization": token,
                                         "Origin": "https://www.aula.dk",
                                         "Referer": "https://www.aula.dk/"},
                                allow_redirects=True, timeout=15,
                            )
                            detail.raise_for_status()
                            description = BeautifulSoup(detail.text, "lxml").find(
                                "div", {"class": "text-user fr-view"})
                            if description:
                                entry["Description"] = description.get_text()
                        except Exception as err:
                            _LOGGER.debug("Course description unavailable (%s)", type(err).__name__)
                    if entry not in courses:
                        courses.append(entry)
        return result

    def ugeBrev(
        self, session, token, week, childuserids, institutionProfiles, username
    ):
        _LOGGER.debug("Getting Min Uddannelse Uge Brev")

        result = session.get(
            MIN_UDDANNELSE_API
            + "/redirect?redirectUrl=https://www.minuddannelse.net?"
            + "assuranceLevel=2"
            + "&childFilter="
            + ",".join(childuserids)
            + "&currentWeekNumber="
            + week
            + "&institutionFilter="
            + ",".join(institutionProfiles)
            + "&isMobileApp=false"
            + "&placement=full"
            + "&sessionUUID="
            + username
            + "&userProfile=guardian",
            headers={"Authorization": token},
            verify=True,
        )

        _html = BeautifulSoup(result.content, "lxml")
        anchor_element = _html.find("div", {"class": "col-sm-8 col-xs-7"})
        anchor_element = anchor_element.find("a")
        elevid = anchor_element.get("href").replace("minuge/", "")

        response = session.get(
            "https://www.minuddannelse.net/api/stamdata/ugeplan/getUgeBreve?"
            + "tidspunkt="
            + week
            + "&elevId="
            + elevid
            + "&_="
            + str(round(time.time())),
            headers={"accept": "application/json"},
        )
        if response.status_code != 200:
            _LOGGER.warning(
                f"MinUddannelse getUgeBreve returned status {response.status_code}"
            )
            return ""
        result = response.json()["ugebreve"][0]["indhold"]

        _html = BeautifulSoup(result, "lxml")
        return _html.getText()

    def opgaveListe(
        self, session, token, week, childuserids, institutionProfiles, username
    ):
        _LOGGER.debug("Getting Min Uddannelse opgave Liste")

        result = session.get(
            MIN_UDDANNELSE_API
            + "/redirect?redirectUrl=https://www.minuddannelse.net?"
            + "assuranceLevel=2"
            + "&childFilter="
            + ",".join(childuserids)
            + "&currentWeekNumber="
            + week
            + "&institutionFilter="
            + ",".join(institutionProfiles)
            + "&isMobileApp=false"
            + "&placement=full"
            + "&sessionUUID="
            + username
            + "&userProfile=guardian",
            headers={"Authorization": token},
            verify=True,
        )

        _html = BeautifulSoup(result.content, "lxml")
        anchor_element = _html.find("div", {"class": "col-sm-8 col-xs-7"})

        if anchor_element is None:
            _LOGGER.debug("Could not find anchor element div")
            return {}

        anchor_link = anchor_element.find("a")
        if anchor_link is None:
            _LOGGER.debug("Could not find anchor link")
            return {}

        elevid = anchor_link.get("href").replace("minuge/", "")

        response = session.get(
            "https://www.minuddannelse.net/api/forloebsafvikling/opgaver/getOpgaveliste?"
            + "tidspunkt="
            + week
            + "&elevId="
            + elevid
            + "&_="
            + str(round(time.time())),
            headers={"accept": "application/json"},
        )
        if response.status_code != 200:
            _LOGGER.warning(
                f"MinUddannelse getOpgaveliste returned status {response.status_code}"
            )
            return {}
        opgaver = response.json()["opgaver"]

        return opgaver
