"""
Aula client
Based on https://github.com/JBoye/HA-Aula
"""
import logging
import requests
import datetime
import requests
from bs4 import BeautifulSoup
import json, re
from .const import API, API_VERSION, MIN_UDDANNELSE_API, MEEBOOK_API

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
            _LOGGER.debug(str(redirects)+" In the login loop...")
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

        # Find the API url in case of a version change
        self.apiurl = API+API_VERSION
        apiver = int(API_VERSION)
        api_success = False
        while api_success == False:
            _LOGGER.debug("Trying API at "+self.apiurl)
            ver = self._session.get(self.apiurl + "?method=profiles.getProfilesByLogin", verify=True)
            if ver.status_code == 410:
                _LOGGER.warning("API was expected at "+self.apiurl+" but responded with HTTP 410.")
                apiver += 1
            elif ver.status_code == 200:
                self._profiles = ver.json()["data"]["profiles"]
                api_success = True
            self.apiurl = API+str(apiver)
        _LOGGER.debug("Found API on "+self.apiurl)
        #
        self._profilecontext = self._session.get(self.apiurl + "?method=profiles.getProfileContext&portalrole=guardian", verify=True).json()['data']['institutionProfile']['relations']
        _LOGGER.debug("LOGIN: " + str(success))

    def update_data(self):
        _LOGGER.debug("Config, schoolschedule: "+str(self._schoolschedule))
        _LOGGER.debug("Config, ugeplaner: "+str(self._ugeplan))
        is_logged_in = False
        if self._session:
            response = self._session.get(self.apiurl + "?method=profiles.getProfilesByLogin", verify=True).json()
            is_logged_in = response["status"]["message"] == "OK"

        _LOGGER.debug("is_logged_in? " + str(is_logged_in))

        if not is_logged_in:
            self.login()

        self._childnames = {}
        self._institutions = {}
        self._childuserids = []
        self._childids = []
        self._children = []
        self._institutionProfiles = []
        for profile in self._profiles:
            for child in profile["children"]:
                self._childnames[child["id"]] = child["name"]
                self._institutions[child["id"]] = child["institutionProfile"]["institutionName"]
                self._children.append(child)
                self._childids.append(str(child["id"]))
                self._childuserids.append(str(child["userId"]))
                self._institutionProfiles.append(str(child["institutionCode"]))
        _LOGGER.debug("Child ids and names: "+str(self._childnames))
        _LOGGER.debug("Child ids and institution names: "+str(self._institutions))
        
        self._daily_overview = {}
        for i, child in enumerate(self._children):
            response = self._session.get(self.apiurl + "?method=presence.getDailyOverview&childIds[]=" + str(child["id"]), verify=True).json()
            if len(response["data"]) > 0:
                self.presence[str(child["id"])] = 1
                self._daily_overview[str(child["id"])] = response["data"][0]
            else:
                _LOGGER.warn("Unable to retrieve presence data from Aula from child with id "+str(child["id"])+". Some data will be missing from sensor entities.")
                self.presence[str(child["id"])] = 0
        _LOGGER.debug("Child ids and presence data status: "+str(self.presence))

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
            res = self._session.post(self.apiurl + "?method=calendar.getEventsByProfileIdsAndResourceIds",data=post_data,headers=headers, verify=True)
            try:
                with open('skoleskema.json', 'w') as skoleskema_json:
                    json.dump(res.text, skoleskema_json)
            except:
               _LOGGER.warn("Got the following reply when trying to fetch calendars: "+str(res.text))
        # End of calendar
        # Ugeplaner:
        if self._ugeplan == True:
            guardian = self._session.get(self.apiurl + "?method=profiles.getProfileContext&portalrole=guardian", verify=True).json()["data"]["userId"]
            #_LOGGER.debug("guardian :"+str(guardian))
            childUserIds = ",".join(self._childuserids)
            widgets = self._session.get(self.apiurl + "?method=profiles.getProfileContext", verify=True).json()["data"]["moduleWidgetConfiguration"]["widgetConfigurations"]
            #_LOGGER.debug("widgetId "+str(widgets))

            for widget in widgets:
                widgetid = str(widget["widget"]["widgetId"])
                widgetname = widget["widget"]["name"]
                _LOGGER.debug("Detected widget "+widgetid+" "+str(widgetname))
                if widgetid == "0004":
                    _LOGGER.debug("Detected the Meebook widget")
                    self.meebook = 1
                    continue
                if widgetid == "0029":
                    _LOGGER.debug("Detected Min Uddannelse widget")
                    self.minuddannelse = 1
                    continue
            if self.meebook == 0 and self.minuddannelse == 0:
                _LOGGER.error("You have enabled ugeplaner, but we cannot find them in Aula.")
            if self.meebook == 1 and self.minuddannelse == 1:
                _LOGGER.error("Multiple sources for ugeplaner is not supported yet.")

            def ugeplan(week,thisnext):
                #self.meebook = 1
                if self.minuddannelse == 1:
                    self._bearertoken = self._session.get(self.apiurl + "?method=aulaToken.getAulaToken&widgetId=0029", verify=True).json()["data"]
                    token = "Bearer "+str(self._bearertoken)
                    get_payload = '/ugebrev?assuranceLevel=2&childFilter='+childUserIds+'&currentWeekNumber='+week+'&isMobileApp=false&placement=narrow&sessionUUID='+guardian+'&userProfile=guardian'
                    ugeplaner = self._session.get(MIN_UDDANNELSE_API + get_payload, headers={"Authorization":token, "accept":"application/json"}, verify=True)
                    #_LOGGER.debug("ugeplaner status_code "+str(ugeplaner.status_code))
                    #_LOGGER.debug("ugeplaner response "+str(ugeplaner.text))
                    for person in ugeplaner.json()["personer"]:
                        ugeplan = person["institutioner"][0]["ugebreve"][0]["indhold"]
                        if thisnext == "this":
                            self.ugep_attr[person["navn"].split()[0]] = ugeplan
                        elif thisnext == "next":
                            self.ugepnext_attr[person["navn"].split()[0]] = ugeplan

                if self.meebook == 1:
                    # Try Meebook:
                    _LOGGER.debug("In the Meebook flow...")
                    self._bearertoken = self._session.get(self.apiurl + "?method=aulaToken.getAulaToken&widgetId=0004", verify=True).json()["data"]
                    token = "Bearer "+str(self._bearertoken)
                    #_LOGGER.debug("Token "+token)
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
                        _LOGGER.warning("Using mock data for Meebook ugeplaner.")
                        mock = '[{"id":490000,"name":"Emilie efternavn","unilogin":"lud...","weekPlan":[{"date":"mandag 28. nov.","tasks":[{"id":3069630,"type":"comment","author":"Met...","group":"3.a - ugeplan","pill":"Ingen fag tilknyttet","content":"I denne uge er der omlagt uge p\u00e5 hele skolen.\n\nMandag har vi \nKlippeklistredag:\n\nMan m\u00e5 gerne have nissehuer p\u00e5 :)\n\nMedbring gerne en god saks, limstift, skabeloner mm. \n\nB\u00f8rnene skal ogs\u00e5 medbringe et vasket syltet\u00f8jsglas eller lign., som vi skal male p\u00e5. S\u00f8rg gerne for at der ikke er m\u00e6rker p\u00e5:-)\n\n1. lektion: Morgenb\u00e5nd med l\u00e6sning/opgaver\n\n2. lektion: \nVi laver f\u00e6lles julenisser efter en bestemt skabelon.\n\n3. - 5. lektion: \nVi julehygger med musik og kreative projekter. Vi pynter vores f\u00e6lles juletr\u00e6, og synger julesange. \n\n6. lektion:\nAfslutning og oprydning.","editUrl":"https://app.meebook.com//arsplaner/dlap//956783//202248"}]},{"date":"tirsdag 29. nov.","tasks":[{"id":3069630,"type":"comment","author":"Met...","group":"3.a - ugeplan","pill":"Ingen fag tilknyttet","content":"Omlagt uge:\n\n1. lektion\nMorgenb\u00e5nd med l\u00e6sning og opgaver.\n\n2. lektion\nVi starter p\u00e5 storylineforl\u00f8b om jul. Vi taler om nisser og danner nissefamilier i klassen.\n\n3.-5. lektion\nVi lave et juleprojekt med filt...\n\n6. lektion\nVi arbejder med en kreativ opgave om v\u00e5benskold.","editUrl":"https://app.meebook.com//arsplaner/dlap//956783//202248"}]},{"date":"onsdag 30. nov.","tasks":[{"id":3069630,"type":"comment","author":"Met...","group":"3.a - ugeplan","pill":"Ingen fag tilknyttet","content":"Omlagt uge:\n\n1. -2. lektion\nVi skal til foredrag med SOS B\u00f8rnebyerne om omvendt julekalender.\n\n3-4. lektion\nVi skriver nissehistorier om nissefamilierne.\n\n5.-6. lektion\nVi laver jule-postel\u00f8b, hvor posterne skal l\u00e6ses med en kodel\u00e6ser.","editUrl":"https://app.meebook.com//arsplaner/dlap//956783//202248"}]},{"date":"torsdag 1. dec.","tasks":[{"id":3069630,"type":"comment","author":"Met...","group":"3.a - ugeplan","pill":"Ingen fag tilknyttet","content":"Omlagt uge:\n\n1. lektion\nMorgenb\u00e5nd med l\u00e6sning og opgaver. \nVi arbejder med l\u00e6s og forst\u00e5 i en julehistorie.\n\n2.-5. lektion\nVi skal arbejde med et kreativt juleprojekt, hvor der laves huse til nisserne.\n\n6. lektion\nSe SOS b\u00f8rnebyernes julekalender og afrunding af dagen.","editUrl":"https://app.meebook.com//arsplaner/dlap//956783//202248"}]},{"date":"fredag 2. dec.","tasks":[{"id":3069630,"type":"comment","author":"Met...","group":"3.a - ugeplan","pill":"Ingen fag tilknyttet","content":"1. lektion\nMorgenb\u00e5nd med l\u00e6sning og opgaver samt julehygge, hvor vi l\u00e6ser julehistorie \n\n2. lektion:\nVi skal lave et julerim og skrive det ind p\u00e5 en flot julenisse samt tegne nissen. \n\n3.-4. lektion\nVi skal lave jule-postel\u00f8b p\u00e5 skolen. \n\n5.. lektion\nVi skal l\u00f8se et hemmeligt kodebrev ved hj\u00e6lp af en kodel\u00e6ser. \n\nVi evaluerer og afrunder ugen.","editUrl":"https://app.meebook.com//arsplaner/dlap//956783//202248"}]}]},{"id":630000,"name":"Ann...","unilogin":"ann...","weekPlan":[{"date":"mandag 28. nov.","tasks":[{"id":3090189,"type":"comment","author":"May...","group":"0C (22/23)","pill":"B\u00f8rnehaveklasse, B\u00f8rnehaveklassen, Dansk, Matematik","content":"I dag skal vi h\u00f8re om jul i Norge og lave Norsk julepynt.\nEfter 12 pausen skal vi h\u00f8re om julen i Danmark f\u00f8r juletr\u00e6et og andestegen.\nVi skal farvel\u00e6gge g\u00e5rdnisserne der passede p\u00e5 g\u00e5rdene i gamle dage.","editUrl":"https://app.meebook.com//arsplaner/dlap//899210//202248"}]},{"date":"tirsdag 29. nov.","tasks":[{"id":3090189,"type":"comment","author":"May...","group":"0C (22/23)","pill":"B\u00f8rnehaveklasse, B\u00f8rnehaveklassen, Dansk, Matematik","content":"I dag skal vi arbejde med julen i Gr\u00f8nland og lave gr\u00f8nlandske julehuse.\nEfter 12 pausen skal vi h\u00f8re om JUletr\u00e6et der flytter ind i de danske stuer. Vi skal tale om hvor det stammer fra og hvad der var p\u00e5 juletr\u00e6et i gamle dage . Blandt andet den spiselige pynt.\nVi taler om Peters jul og at der ikke altid har v\u00e6ret en stjerne i toppen. Vi klipper storke til juletr\u00e6stoppen","editUrl":"https://app.meebook.com//arsplaner/dlap//899210//202248"}]},{"date":"onsdag 30. nov.","tasks":[{"id":3090189,"type":"comment","author":"May...","group":"0C (22/23)","pill":"B\u00f8rnehaveklasse, B\u00f8rnehaveklassen, Dansk, Matematik","content":"I dag st\u00e5r den p\u00e5 Jul i Finland og finske juletraditioner. Vi klipper finske julestjerner.\nEfter pausen skal vi arbejde videre med jul og julepynt gennem tiden i dk. \nVi skal tale om hvorfor der er flag, trompeter og trommer p\u00e5 tr\u00e6et (krigen i 1864) og vi skal lave gammeldags silkeroser og musetrapper til tr\u00e6et","editUrl":"https://app.meebook.com//arsplaner/dlap//899210//202248"}]},{"date":"torsdag 1. dec.","tasks":[{"id":3090189,"type":"comment","author":"May...","group":"0C (22/23)","pill":"B\u00f8rnehaveklasse, B\u00f8rnehaveklassen, Dansk, Matematik","content":"I dag skal vi p\u00e5 en juletur med hygge og posl\u00f8b til trylleskoven \nBussen k\u00f8rer os derud kl 10 og vi er senest tilbage n\u00e5r skoledagen slutter .\nHusk at f\u00e5 varmt praktisk t\u00f8j p\u00e5 og en turtaske med en let tilg\u00e6ngelig madpakke der kan spises i det fri. Regnbukser eller overtr\u00e6ksbukser s\u00e5 man kan sidde p\u00e5 jorden.","editUrl":"https://app.meebook.com//arsplaner/dlap//899210//202248"}]},{"date":"fredag 2. dec.","tasks":[{"id":3090189,"type":"comment","author":"May...","group":"0C (22/23)","pill":"B\u00f8rnehaveklasse, B\u00f8rnehaveklassen, Dansk, Matematik","content":"Klippe/ klistre dag .\nHusk at tage lim, saks og kaffe m.m., kop og tallerkner med hjemmefra. Hvis i tager kage med er det til en buffet i klassen.","editUrl":"https://app.meebook.com//arsplaner/dlap//899210//202248"}]}]}]'
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
                                    content = re.sub(r"([0-9]+)(\.)", r"\1\.", task["content"])
                                    ugep = ugep+content+"<br><br>"
                            else:
                                ugep = ugep+"-"
                        try:
                            name = person["name"].split()[0]
                        except:
                            name = person["name"]
                        if thisnext == "this":
                            self.ugep_attr[name] = ugep
                        elif thisnext == "next":
                            self.ugepnext_attr[name] = ugep

            now = datetime.datetime.now() + datetime.timedelta(weeks=1)
            thisweek = datetime.datetime.now().strftime('%Y-W%W')
            nextweek = now.strftime("%Y-W%W")
            ugeplan(thisweek,"this")
            ugeplan(nextweek,"next")
            #_LOGGER.debug("End result of ugeplan object: "+str(self.ugep_attr))
        # End of Ugeplaner
        return True