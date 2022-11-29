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
    meebook = 0
    minuddannelse = 0
    presence = {}
    ugep_attr = {}
    ugepnext_attr = {}
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

        user_data = { 'username': self._username, 'password': self._password, 'selected-aktoer': "KONTAKT" }
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
        self._profilecontext = self._session.get(API + "?method=profiles.getProfileContext&portalrole=guardian", verify=True).json()['data']['institutionProfile']['relations']

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
                self.presence[str(child["id"])] = 1
                self._daily_overview[str(child["id"])] = response["data"][0]
            else:
                _LOGGER.warn("Unable to retrieve presence data from Aula from child with id "+str(child["id"])+". Some data will be missing from sensor entities.")
                self.presence[str(child["id"])] = 0

        # Calendar:
        if self._schoolschedule == True:
            instProfileIds = ",".join(self._childids)
            csrf_token = self._session.cookies.get_dict()["Csrfp-Token"]

            headers = {'csrfp-token': csrf_token, 'content-type': 'application/json'}
            start = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d 00:00:00.0000%z')
            _end = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=14)
            end = _end.strftime('%Y-%m-%d 00:00:00.0000%z')
            post_data = '{"instProfileIds":['+instProfileIds+'],"resourceIds":[],"start":"'+start+'","end":"'+end+'"}'
            #_LOGGER.debug("Fetching calendars...")
            #_LOGGER.debug("Calendar post-data: "+str(post_data))
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
            #_LOGGER.debug("guardian :"+str(guardian))
            childUserIds = ",".join(self._childuserids)

            widgets = self._session.get(API + "?method=profiles.getProfileContext", verify=True).json()["data"]["moduleWidgetConfiguration"]["widgetConfigurations"]
            #_LOGGER.debug("widgetId "+str(widgets))

            for widget in widgets:
                widgetid = str(widget["widget"]["widgetId"])
                widgetname = widget["widget"]["name"]
                _LOGGER.debug("Widget "+widgetid+" "+str(widgetname))
                if widgetid == "0004":
                    _LOGGER.debug("Detected the Meebook widget")
                    self.meebook = 1
                    break
                if widgetid == "0029":
                    _LOGGER.debug("Detected Min Uddannelse widget")
                    self.minuddannelse = 1
                    break
            if self.meebook == 0 and self.minuddannelse == 0:
                _LOGGER.error("You have enabled ugeplaner, but we cannot find them in Aula.")
            if self.meebook == 1 and self.minuddannelse == 1:
                _LOGGER.error("Multiple sources for ugenoter is not supported yet.")

            def ugeplan(week,thisnext):
                #self.meebook = 1
                if self.minuddannelse == 1:
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

                if self.meebook == 1:
                    # Try Meebook:
                    _LOGGER.debug("In the Meebook flow...")
                    self._bearertoken = self._session.get(API + "?method=aulaToken.getAulaToken&widgetId=0004", verify=True).json()["data"]
                    token = "Bearer "+str(self._bearertoken)
                    #_LOGGER.debug("Token "+token)
                    #self.ugep_attr = {}
                    #self.ugepnext_attr = {}
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
                    #_LOGGER.debug("get_payload: "+get_payload)
                    
                    mock = 0
                    if mock == 1:
                        mock ='[{"id":497514,"name":"Emilie efternavn","unilogin":"lud?????","weekPlan":[{"date":"mandag 14. nov.","tasks":[{"id":3038052,"type":"comment","author":"Met...","group":"3.a - ugeplan","pill":"Ingen fag tilknyttet","content":"IDR\u00c6T:\nMusik og bev\u00e6gelse.\nVi er i hallen, hvor vi arbejder med rytme, bev\u00e6gelse og koordination.\nHUSK idr\u00e6tst\u00f8j og h\u00e5ndkl\u00e6de (evt. indend\u00f8rs kondisko).\n\nDANSK\n\nVi skal i denne uge:\n\nL\u00c6SE:\nI skolen skal vi arbejde i systemet Tid til l\u00e6seforst\u00e5else. Vi arbejder med at l\u00e6se og forst\u00e5 forskellige teksttyper ud fra nogle forskellige sp\u00f8rgsm\u00e5lstyper. Ligeledes har vi fokus p\u00e5 at holde orden i h\u00e6ftet og at formulere os i flotte s\u00e6tninger, n\u00e5r vi svarer p\u00e5 sp\u00f8rgsm\u00e5l. Dette m\u00e5 i meget gerne have fokus p\u00e5 mundtligt i den kommende periode. N\u00e5r I stiller b\u00f8rnene et sp\u00f8rgsm\u00e5l, s\u00e5 bed dem gerne svare med en fuld s\u00e6tning. Fx: Hvor mange appelsiner skal man bruge til frugtsalaten? Mange af b\u00f8rnene ville blot svare: 2. Men her vil det v\u00e6re fint at lade dem svare: Til frugtsalaten skal man bruge 2 appelsiner. \n\nVi gennemg\u00e5r opgaven fra sidste uge, hvor vi arbejdede med en opskrift p\u00e5 Frugtsalat. B\u00f8rnene f\u00e5r opskriften med hjem, da de meget gerne ville pr\u00f8ve at lave den. Vi skal bl.a. arbejde med digtet/sangen Snemand Frost og Fr\u00f8ken T\u00f8 i denne uge. \n\nHusk fortsat at l\u00e6se hjemme i 20 min. og at skrive l\u00e6sekort.\n\nSTAVEVEJEN:\nVi arbejder med siderne 28-30. Der m\u00e5 gerne arbejdes med Stavevejen.dk derhjemme.\n\nDEN NATIONALE OVERGANGSTEST I L\u00c6SNING:\nVi laver demotesten i skolen og taler om testens opgavetyper. Det er ogs\u00e5 en god ide at lave den hjemme. Vi laver derefter selve testen.","editUrl":"https://app.meebook.com//arsplaner/dlap//956783//202246"}]},{"date":"tirsdag 15. nov.","tasks":[]},{"date":"onsdag 16. nov.","tasks":[{"id":3038052,"type":"comment","author":"Met...","group":"3.a - ugeplan","pill":"Ingen fag tilknyttet","content":"BIBLIOTEKET:\nVi skal have et lille opl\u00e6g af vores bibliotekar om gode b\u00f8ger. \n\nHusk b\u00f8ger s\u00e5 i kan l\u00e5ne nogle nye.","editUrl":"https://app.meebook.com//arsplaner/dlap//956783//202246"}]},{"date":"torsdag 17. nov.","tasks":[{"id":3038052,"type":"comment","author":"Met...","group":"3.a - ugeplan","pill":"Ingen fag tilknyttet","content":"Teknologiforst\u00e5else: Vi skal arbejde med Jamboard og lave en digital planche om internettet.\n\nHistorie:\nArbejde med vores opgaver i emnet om de f\u00f8rste mennesker og j\u00e6ger/samler samfundet.","editUrl":"https://app.meebook.com//arsplaner/dlap//956783//202246"}]},{"date":"fredag 18. nov.","tasks":[]}]},{"id":633968,"name":"Ann...","unilogin":"ann?????","weekPlan":[{"date":"mandag 14. nov.","tasks":[{"id":3028973,"type":"comment","author":"May...","group":"0C (22/23)","pill":"B\u00f8rnehaveklasse, B\u00f8rnehaveklassen, Dansk, Matematik","content":"Dansk: Vi starter med bogstavet k som lyd og form\nUSU\nMat: Godtfreds dyrehandel og \u00f8velse af talr\u00e6kken fra 10-20. \u00d8v gerne derhjemme.\nBiblioteket","editUrl":"https://app.meebook.com//arsplaner/dlap//899210//202246"}]},{"date":"tirsdag 15. nov.","tasks":[{"id":3028973,"type":"comment","author":"May...","group":"0C (22/23)","pill":"B\u00f8rnehaveklasse, B\u00f8rnehaveklassen, Dansk, Matematik","content":"Mat: Vi arbejder fortsat i Kontext mat bog med geometri og brug af en lineal.\nMusik, leg og bev\u00e6gelse.\nDansk: Bogstavsbanko med pr\u00e6mier","editUrl":"https://app.meebook.com//arsplaner/dlap//899210//202246"}]},{"date":"onsdag 16. nov.","tasks":[{"id":3028973,"type":"comment","author":"May...","group":"0C (22/23)","pill":"B\u00f8rnehaveklasse, B\u00f8rnehaveklassen, Dansk, Matematik","content":"Dansk: Bogstavet U \nsom lyd og bev\u00e6gelse.\nUSU\nMusik, leg og bev\u00e6gelse..","editUrl":"https://app.meebook.com//arsplaner/dlap//899210//202246"}]},{"date":"torsdag 17. nov.","tasks":[{"id":3028973,"type":"comment","author":"May...","group":"0C (22/23)","pill":"B\u00f8rnehaveklasse, B\u00f8rnehaveklassen, Dansk, Matematik","content":"Dansk: \nRep side i Fandango mini af bogstaverne f,k og u\nKrea: Vi er s\u00e5 heldige at Katharinas mor har v\u00e6ret forbi med kalenderlys til dekorationer, s\u00e5 vi skal lave fine juledekorationer.\nMat: Figurer og \n sammensatte figurer.","editUrl":"https://app.meebook.com//arsplaner/dlap//899210//202246"}]},{"date":"fredag 18. nov.","tasks":[{"id":3028973,"type":"comment","author":"May...","group":"0C (22/23)","pill":"B\u00f8rnehaveklasse, B\u00f8rnehaveklassen, Dansk, Matematik","content":"Dansk: L\u00e6seside i Fandango mini med huskeordene ER og IKKE. De m\u00e5 meget gerne \u00f8ves derhjemme.\nL\u00e6sesiden kommer med hjem til at l\u00e6se hjemme.\nI skal \u00f8ve huskeordene og l\u00e6se hvad der st\u00e5r i kasse nr 1. Resten af siden skal i ikke g\u00f8re mere ud af.\nVi repeterer p\u00e5 mandag \nIdr\u00e6t.\nEngelsk","editUrl":"https://app.meebook.com//arsplaner/dlap//899210//202246"}]}]}]'
                        data = json.loads(mock, strict=False)
                    else:
                        response = requests.get(MEEBOOK_API + get_payload, headers=headers, verify=True)
                        data = json.loads(response.text, strict=False)
                        #_LOGGER.debug("Meebook ugeplan raw response from week "+week+": "+str(response.text))
                    
                    for person in data:
                        _LOGGER.debug("Meebook ugeplan for "+person["name"])
                        ugep = ''
                        ugeplan = person["weekPlan"]
                        for day in ugeplan:
                            ugep = ugep+"<h3>"+day["date"]+"</h3>"
                            if len(day["tasks"]) > 0:
                                for task in day["tasks"]:
                                    if not task["pill"] == "Ingen fag tilknyttet":
                                        ugep = ugep+"<b>"+task["pill"]+"</b><br>"
                                    ugep = ugep+task["author"]+"<br><br>"
                                    ugep = ugep+task["content"]+"<br><br>"
                            else:
                                ugep = ugep+"-"
                        #_LOGGER.debug(ugep)
                        try:
                            name = person["name"].split()[0]
                        except:
                            name = person["name"]
                        if thisnext == "this":
                            self.ugep_attr[name] = ugep
                            #_LOGGER.debug("ugeplan object "+str(self.ugep_attr))
                            #self.ugep_attr[person["name"]].split()[0] = ugep
                        elif thisnext == "next":
                            #self.ugepnext_attr[person["name"]].split()[0] = ugep
                            self.ugepnext_attr[name] = ugep
                            #_LOGGER.debug("ugeplan_next object "+str(self.ugepnext_attr))

            now = datetime.datetime.now() + datetime.timedelta(weeks=1)
            thisweek = datetime.datetime.now().strftime('%Y-W%W')
            nextweek = now.strftime("%Y-W%W")
            ugeplan(thisweek,"this")
            ugeplan(nextweek,"next")
            _LOGGER.debug("End result of ugeplan object: "+str(self.ugep_attr))
        # End of Ugeplaner
        return True