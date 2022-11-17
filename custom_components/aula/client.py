"""
Aula client
Based on https://github.com/JBoye/HA-Aula
"""
import logging
import requests
import datetime
import requests
from bs4 import BeautifulSoup
import json
from .const import API, MIN_UDDANNELSE_API, MEEBOOK_API

_LOGGER = logging.getLogger(__name__)

class Client:
    def __init__(self, username, password, schoolschedule, ugeplan):
        self._username = username
        self._password = password
        self._session = None
        self._schoolschedule = schoolschedule
        self._ugeplan = ugeplan

    def login(self):
        _LOGGER.debug("Logging in")
        self._session = requests.Session()
        response = self._session.get('https://login.aula.dk/auth/login.php?type=unilogin', verify=True)

        user_data = { 'username': self._username, 'password': self._password }
        redirects = 0
        success = False
        url = ''
        while success == False and redirects < 10:
            html = BeautifulSoup(response.text, 'lxml')
            url = html.form['action']
            post_data = {}
            for input in html.find_all('input'):
                if(input.has_attr('name') and input.has_attr('value')):
                    post_data[input['name']] = input['value']
                    for key in user_data:
                        if(input.has_attr('name') and input['name'] == key):
                            post_data[key] = user_data[key]

            response = self._session.post(url, data = post_data, verify=True)
            if response.url == 'https://www.aula.dk:443/portal/':
                success = True
            redirects += 1

        self._profiles = self._session.get(API + "?method=profiles.getProfilesByLogin", verify=True).json()["data"]["profiles"]
        self._session.get(API + "?method=profiles.getProfileContext&portalrole=guardian", verify=True)
        _LOGGER.debug("LOGIN: " + str(success))

    def update_data(self):
        _LOGGER.debug("self._schoolschedule "+str(self._schoolschedule))
        _LOGGER.debug("self._ugeplan "+str(self._ugeplan))
        is_logged_in = False
        if self._session:
            response = self._session.get(API + "?method=profiles.getProfilesByLogin", verify=True).json()
            is_logged_in = response["status"]["message"] == "OK"

        _LOGGER.debug("is_logged_in? " + str(is_logged_in))

        if not is_logged_in:
            self.login()

        self._childuserids = []
        self._childids = []
        self._children = []
        self._institutionProfiles = []
        for profile in self._profiles:
            for child in profile["children"]:
                self._children.append(child)
                self._childids.append(str(child["id"]))
                self._childuserids.append(str(child["userId"]))
                self._institutionProfiles.append(str(child["institutionCode"]))

        self._daily_overview = {}
        for i, child in enumerate(self._children):
            response = self._session.get(API + "?method=presence.getDailyOverview&childIds[]=" + str(child["id"]), verify=True).json()
            if len(response["data"]) > 0:
                msg = response["data"][0]
                self._daily_overview[str(child["id"])] = response["data"][0]

        # Calendar:
        if self._schoolschedule == True:
            instProfileIds = ",".join(self._childids)
            csrf_token = self._session.cookies.get_dict()["Csrfp-Token"]

            headers = {'csrfp-token': csrf_token, 'content-type': 'application/json'}
            start = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d 00:00:00.0000%z')
            _end = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=14)
            end = _end.strftime('%Y-%m-%d 00:00:00.0000%z')
            post_data = '{"instProfileIds":['+instProfileIds+'],"resourceIds":[],"start":"'+start+'","end":"'+end+'"}'
            _LOGGER.debug("Fetching calendars...")
            _LOGGER.debug("Calendar post-data: "+str(post_data))
            res = self._session.post(API + "?method=calendar.getEventsByProfileIdsAndResourceIds",data=post_data,headers=headers, verify=True)
            try:
                with open('skoleskema.json', 'w') as skoleskema_json:
                    json.dump(res.text, skoleskema_json)
            except:
               _LOGGER.warn("Got the following reply when trying to fetch calendars: "+str(res.text))
        # End of calendar
        # Ugeplaner:
        if self._ugeplan == True:
            guardian = self._session.get(API + "?method=profiles.getProfileContext&portalrole=guardian", verify=True).json()["data"]["userId"]
            _LOGGER.debug("guardian :"+str(guardian))
            childUserIds = ",".join(self._childuserids)

            widgets = self._session.get(API + "?method=profiles.getProfileContext", verify=True).json()["data"]["moduleWidgetConfiguration"]["widgetConfigurations"]
            _LOGGER.debug("widgetId "+str(widgets))
            for widget in widgets:
                widgetid = str(widget["widget"]["widgetId"])
                widgetname = widget["widget"]["name"]
                _LOGGER.debug("Widget "+widgetid+" "+str(widgetname))
                if widgetid == "0004":
                    _LOGGER.debug("Setting meebook to 1")
                    meebook = 1
                    break
                if widgetid == "0029":
                    _LOGGER.debug("Setting meebook to 0")
                    meebook = 0
                    break

            def ugeplan(week,thisnext):
                meebook = 1
                if meebook == 0:
                    self._bearertoken = self._session.get(API + "?method=aulaToken.getAulaToken&widgetId=0029", verify=True).json()["data"]
                    token = "Bearer "+str(self._bearertoken)
                    self.ugep_attr = {}
                    self.ugepnext_attr = {}
                    get_payload = '/ugebrev?assuranceLevel=2&childFilter='+childUserIds+'&currentWeekNumber='+week+'&isMobileApp=false&placement=narrow&sessionUUID='+guardian+'&userProfile=guardian'
                    ugeplaner = self._session.get(MIN_UDDANNELSE_API + get_payload, headers={"Authorization":token, "accept":"application/json"}, verify=True)
                    _LOGGER.debug("ugeplaner status_code "+str(ugeplaner.status_code))
                    _LOGGER.debug("ugeplaner response "+str(ugeplaner.text))
                    for person in ugeplaner.json()["personer"]:
                        ugeplan = person["institutioner"][0]["ugebreve"][0]["indhold"]
                        if thisnext == "this":
                            self.ugep_attr[person["navn"]] = ugeplan
                        elif thisnext == "next":
                            self.ugepnext_attr[person["navn"]] = ugeplan

                if meebook == 1:
                    # Try Meebook:
                    _LOGGER.debug("In the Meebook flow...")
                    self._bearertoken = self._session.get(API + "?method=aulaToken.getAulaToken&widgetId=0004", verify=True).json()["data"]
                    token = "Bearer "+str(self._bearertoken)
                    _LOGGER.debug("Token "+token)
                    self.ugep_attr = {}
                    self.ugepnext_attr = {}
                    headers = {
                        "authority": "app.meebook.com",
                        "accept": "application/json",
                        "authorization": token,
                        "dnt": "1",
                        "origin": "https://www.aula.dk",
                        "referer": "https://www.aula.dk/",
                        "sessionuuid": self._username,
                        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Safari/537.36",
                        "x-version": "1.0"
                    }
                    childFilter = "&childFilter[]=".join(self._childuserids)
                    institutionFilter = "&institutionFilter[]=".join(self._institutionProfiles)
                    get_payload = '/relatedweekplan/all?currentWeekNumber='+week+'&userProfile=guardian&childFilter[]='+childFilter+'&institutionFilter[]='+institutionFilter

                    _LOGGER.debug("get_payload: "+get_payload)
                    #ugeplaner = self._session.get(MEEBOOK_API + get_payload, headers={"Authorization":token, "accept":"application/json", "sessionuuid":self._username}, verify=True)
                    ugeplaner = requests.get(MEEBOOK_API + get_payload, headers=headers, verify=True)
                    _LOGGER.debug("Meebook ugeplaner status_code "+str(ugeplaner.status_code))
                    _LOGGER.debug("Meebook ugeplaner response "+str(ugeplaner.text))
                    try:
                        for person in ugeplaner.json():
                            ugeplan = person["weekPlan"]["tasks"][0]["content"]
                            _LOGGER.debug("Meebook ugeplan for "+str(person["name"]))
                            if thisnext == "this":
                                self.ugep_attr[person["name"]] = ugeplan
                            elif thisnext == "next":
                                self.ugepnext_attr[person["name"]] = ugeplan
                    except:
                        _LOGGER.warn("Could not parse ugeplaner (Meebook)")

            now = datetime.datetime.now() + datetime.timedelta(weeks=1)
            thisweek = datetime.datetime.now().strftime('%Y-W%W')
            nextweek = now.strftime("%Y-W%W")
            ugeplan(thisweek,"this")
            ugeplan(nextweek,"next")
        # End of Ugeplaner
        return True