import requests, sys, logging
from bs4 import BeautifulSoup
import requests, json, base64, sys
from urllib.parse import urlparse, parse_qs, urljoin

sys.path.append("..")
sys.path.append(".")
from BrowserClient.Helpers import process_args, get_default_args
from BrowserClient.Helpers import (
    get_authentication_code,
    process_args,
    generate_nem_login_parameters,
    get_default_args,
    choose_between_multiple_identitites,
)

_LOGGER = logging.getLogger(__name__)

argparser = get_default_args()
args = argparser.parse_args()
method, user_id, password, proxy = process_args(args)


session = requests.Session()
if proxy:
    session.proxies.update({"http": f"socks5://{proxy}", "https": f"socks5://{proxy}"})

# Step 1: GET the login page
url1 = "https://login.aula.dk/auth/login.php?type=unilogin"
# print(f"[DEBUG] GET {url1}")
resp1 = session.get(url1)
# print(f"[DEBUG] Response status: {resp1.status_code}")
# print(f"[DEBUG] Response headers: {resp1.headers}")
soup = BeautifulSoup(resp1.text, "html.parser")
form = soup.find("form", {"action": True})
if not form:
    _LOGGER.error("Could not find login form.")
    sys.exit(1)
login_url = form["action"]
# print(f"[DEBUG] Found login form action: {login_url}")

# Step 2: POST to submit the MitID button
post_data = {"selectedIdp": "nemlogin3"}
# print(f"[DEBUG] POST {login_url} with data: {post_data}")
resp2 = session.post(login_url, data=post_data, allow_redirects=False)
# print(f"[DEBUG] Response status: {resp2.status_code}")
# print(f"[DEBUG] Response headers: {resp2.headers}")

# Step 3: GET the first redirect location
if "Location" not in resp2.headers:
    _LOGGER.error("[ERROR] No redirect after POST to login form.")
    sys.exit(1)
next_url = urljoin(resp2.url, resp2.headers["Location"])
# print(f"[DEBUG] Redirect Location: {next_url}")
# print(f"[DEBUG] GET {next_url}")
resp3 = session.get(next_url, allow_redirects=False)
# print(f"[DEBUG] Response status: {resp3.status_code}")
# print(f"[DEBUG] Response headers: {resp3.headers}")

# Step 4: GET the next redirect location
if "Location" not in resp3.headers:
    _LOGGER.error("[ERROR] No redirect after GET to first redirect location.")
    sys.exit(1)
next_url2 = urljoin(resp3.url, resp3.headers["Location"])
# print(f"[DEBUG] Redirect Location: {next_url2}")
# print(f"[DEBUG] GET {next_url2}")
resp4 = session.get(next_url2, allow_redirects=False)
# print(f"[DEBUG] Response status: {resp4.status_code}")
# print(f"[DEBUG] Response headers: {resp4.headers}")

# Step 5: GET the final redirect location
if "Location" not in resp4.headers:
    _LOGGER.error("[ERROR] No redirect after GET to second redirect location.")
    sys.exit(1)
next_url3 = urljoin(resp4.url, resp4.headers["Location"])
# print(f"[DEBUG] Redirect Location: {next_url3}")
# print(f"[DEBUG] GET {next_url3}")
resp5 = session.get(next_url3, allow_redirects=False)
# print(f"[DEBUG] Response status: {resp5.status_code}")
# print(f"[DEBUG] Response headers: {resp5.headers}")

# Step 6: Follow /login/mitid redirect if present
if "Location" in resp5.headers:
    next_url4 = urljoin(next_url3, resp5.headers["Location"])
    #   print(f"[DEBUG] Redirect Location: {next_url4}")
    #  print(f"[DEBUG] GET {next_url4}")
    resp6 = session.get(next_url4, allow_redirects=False)
    # print(f"[DEBUG] Response status: {resp6.status_code}")
#   print(f"[DEBUG] Response headers: {resp6.headers}")
# print(f"[DEBUG] Response body: {resp6.text}")
else:
    _LOGGER.debug(f"No further redirect. Final response body:")
    # print(resp5.text)


# Use the correct response for parsing the token
final_html = resp6.text if "resp6" in locals() else resp5.text
soup = BeautifulSoup(final_html, "lxml")
token_input = soup.find("input", {"name": "__RequestVerificationToken"})

if token_input:
    request_verification_token = token_input.get("value")
    _LOGGER.debug(f"__RequestVerificationToken: {request_verification_token}")

    # Prepare dynamic POST data for /login/mitid/initialize
    # You may need to adjust these fields based on the actual HTML and flow
    post_url = "https://nemlog-in.mitid.dk/login/mitid/initialize"
    post_headers = {
        "accept": "*/*",
        "accept-encoding": "gzip, deflate, br, zstd",
        "accept-language": "en-US,en;q=0.9,da;q=0.8",
        "cache-control": "no-cache",
        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        "origin": "https://nemlog-in.mitid.dk",
        "pragma": "no-cache",
        "referer": "https://nemlog-in.mitid.dk/login/mitid",
        "sec-ch-ua": '"Not;A=Brand";v="99", "Google Chrome";v="139", "Chromium";v="139"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
        "x-requested-with": "XMLHttpRequest",
    }

    # Example form data, you may need to add more fields depending on the flow
    post_data = {
        "__RequestVerificationToken": request_verification_token
        # Add other required fields here
    }

    # print(f"[DEBUG] POST {post_url} with data: {post_data}")
    resp_init = session.post(post_url, headers=post_headers, data=post_data)
    # print(f"[DEBUG] Response status: {resp_init.status_code}")
    # print(f"[DEBUG] Response headers: {resp_init.headers}")
    _LOGGER.debug(f"Response body: {resp_init.text}")

    # Extract Aux value from JSON response
    resp_init_json = resp_init.json()
    if isinstance(resp_init_json, str):
        resp_init_json = json.loads(resp_init_json)
    aux_value = resp_init_json.get("Aux")
    aux = json.loads(base64.b64decode(aux_value).decode())

authorization_code = get_authentication_code(session, aux, method, user_id, password)
_LOGGER.info(f"Your MitID authorization code was ({authorization_code})")


session_storage_active_session_uuid = session.cookies.get("SessionUuid", "")
session_storage_active_challenge = session.cookies.get("Challenge", "")

params = {
    "__RequestVerificationToken": request_verification_token,
    "NewCulture": "",
    "MitIDUseConfirmed": "True",
    "MitIDAuthCode": authorization_code,
    "MitIDAuthenticationCancelled": "",
    "MitIDCoreClientError": "",
    "SessionStorageActiveSessionUuid": session_storage_active_session_uuid,
    "SessionStorageActiveChallenge": session_storage_active_challenge,
}
request = session.post("https://nemlog-in.mitid.dk/login/mitid", data=params)

soup = BeautifulSoup(request.text, features="html.parser")

# print(request.text)
# User has more than one login option
if request.url == "https://nemlog-in.mitid.dk/loginoption":
    request, soup = choose_between_multiple_identitites(session, request, soup)

relay_state = soup.find("input", {"name": "RelayState"}).get("value")
saml_response = soup.find("input", {"name": "SAMLResponse"}).get("value")

params = {"RelayState": relay_state, "SAMLResponse": saml_response}

# Prepare headers for the SAML POST
saml_post_headers = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
    "accept-encoding": "gzip, deflate, br, zstd",
    "accept-language": "en-US,en;q=0.9,da;q=0.8",
    "cache-control": "max-age=0",
    "content-type": "application/x-www-form-urlencoded",
    "origin": "https://nemlog-in.mitid.dk",
    "referer": "https://nemlog-in.mitid.dk/login/mitid",
    "sec-ch-ua": '"Not;A=Brand";v="99", "Google Chrome";v="139", "Chromium";v="139"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "cross-site",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
}

# Add cookies to the headers
cookie_header = "; ".join([f"{c.name}={c.value}" for c in session.cookies])
if cookie_header:
    saml_post_headers["cookie"] = cookie_header

# Make the POST request to broker endpoint (should return 302 redirect)
request = session.post(
    "https://broker.unilogin.dk/auth/realms/broker/broker/nemlogin3/endpoint",
    data=params,
    headers=saml_post_headers,
    allow_redirects=False,  # Don't follow redirects automatically
)

_LOGGER.debug(f"Response Status: {request.status_code}")
_LOGGER.debug(f"Response Headers: {dict(request.headers)}")

# Extract the redirect location (action URL)

action_url = request.headers["Location"]
_LOGGER.debug(f"Extracted action URL: {action_url}")

# Now follow the redirect to get the final form
final_request = session.get(action_url)

_LOGGER.debug(f"Final request status: {final_request.status_code}")
_LOGGER.debug(f"Final response content length: {len(final_request.text)}")

# Parse the response to extract form data and dynamic parameters
soup = BeautifulSoup(final_request.text, "html.parser")

# Extract dynamic parameters from the current URL or form
parsed_url = urlparse(final_request.url)
query_params = parse_qs(parsed_url.query)

# Extract session_code, execution, client_id, and tab_id from the response or URL
session_code = query_params.get("session_code", [""])[0]
execution = query_params.get("execution", [""])[0]
client_id = query_params.get("client_id", [""])[0]
tab_id = query_params.get("tab_id", [""])[0]

# If not found in URL, try to extract from form action or hidden inputs
if not session_code or not execution:
    form = soup.find("form")
    if form:
        form_action = form.get("action", "")

        # Parse the form action URL to get the parameters
        if form_action:
            parsed_form_url = urlparse(form_action)
            form_query_params = parse_qs(parsed_form_url.query)

            if not session_code:
                session_code = form_query_params.get("session_code", [""])[0]
            if not execution:
                execution = form_query_params.get("execution", [""])[0]
            if not client_id:
                client_id = form_query_params.get("client_id", [""])[0]
            if not tab_id:
                tab_id = form_query_params.get("tab_id", [""])[0]

        # Also check hidden inputs as backup
        for input_elem in form.find_all("input", type="hidden"):
            name = input_elem.get("name", "")
            value = input_elem.get("value", "")
            if "session" in name.lower() and not session_code:
                session_code = value
            elif "execution" in name.lower() and not execution:
                execution = value

# Construct the post-broker-login URL
post_broker_url = f"https://broker.unilogin.dk/auth/realms/broker/login-actions/post-broker-login?session_code={session_code}&execution={execution}&client_id={client_id}&tab_id={tab_id}"

# Headers for the post-broker-login request
post_broker_headers = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "accept-encoding": "gzip, deflate, br, zstd",
    "accept-language": "en-US,en;q=0.9,da;q=0.8",
    "cache-control": "no-cache",
    "connection": "keep-alive",
    "content-length": "0",
    "content-type": "application/x-www-form-urlencoded",
    "host": "broker.unilogin.dk",
    "origin": "null",
    "pragma": "no-cache",
    "referrer-policy": "no-referrer",
    "sec-ch-ua": '"Not;A=Brand";v="99", "Google Chrome";v="139", "Chromium";v="139"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "same-origin",
    "upgrade-insecure-requests": "1",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
}

# Add existing cookies
cookie_header = "; ".join([f"{c.name}={c.value}" for c in session.cookies])
if cookie_header:
    post_broker_headers["cookie"] = cookie_header

_LOGGER.debug(f"Making POST request to: {post_broker_url}")

# Make the POST request (expecting 302 redirect)
post_broker_response = session.post(
    post_broker_url,
    headers=post_broker_headers,
    data={},  # Empty form data as content-length is 0
    allow_redirects=False,
)

_LOGGER.debug(f"Post-broker response status: {post_broker_response.status_code}")
_LOGGER.debug(f"Post-broker response headers: {dict(post_broker_response.headers)}")

# Check for redirect location
if "Location" in post_broker_response.headers:
    after_post_broker_url = post_broker_response.headers["Location"]
    _LOGGER.debug(f"Redirect to after-post-broker-login URL: {after_post_broker_url}")

    # Follow the redirect to the after-post-broker-login endpoint
    after_response = session.get(after_post_broker_url)
    _LOGGER.debug(f"After-post-broker response status: {after_response.status_code}")
    _LOGGER.debug(
        f"After-post-broker response content length: {len(after_response.text)}"
    )

    # Parse the response to extract SAML response and RelayState for Aula
    after_soup = BeautifulSoup(after_response.text, "html.parser")

    # Look for SAML response form
    saml_form = after_soup.find("form")
    if saml_form:
        saml_response_input = saml_form.find("input", {"name": "SAMLResponse"})
        relay_state_input = saml_form.find("input", {"name": "RelayState"})

        if saml_response_input and relay_state_input:
            final_saml_response = saml_response_input.get("value")
            final_relay_state = relay_state_input.get("value")

            # Prepare headers for the final SAML POST to Aula
            aula_saml_headers = {
                "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                "accept-encoding": "gzip, deflate, br, zstd",
                "accept-language": "en-US,en;q=0.9,da;q=0.8",
                "cache-control": "no-cache",
                "content-type": "application/x-www-form-urlencoded",
                "origin": "null",
                "pragma": "no-cache",
                "priority": "u=0, i",
                "sec-ch-ua": '"Not;A=Brand";v="99", "Google Chrome";v="139", "Chromium";v="139"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "sec-fetch-dest": "document",
                "sec-fetch-mode": "navigate",
                "sec-fetch-site": "cross-site",
                "upgrade-insecure-requests": "1",
                "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
            }

            # Add cookies for Aula request
            aula_cookie_header = "; ".join(
                [
                    f"{c.name}={c.value}"
                    for c in session.cookies
                    if "aula.dk" in c.domain or c.domain == ""
                ]
            )
            if aula_cookie_header:
                aula_saml_headers["cookie"] = aula_cookie_header

            # Prepare the SAML POST data
            aula_saml_data = {
                "SAMLResponse": final_saml_response,
                "RelayState": final_relay_state,
            }

            _LOGGER.debug(f"Making final SAML POST to Aula...")

            # Make the final POST request to Aula (expecting 303 redirect)
            aula_response = session.post(
                "https://login.aula.dk/simplesaml/module.php/saml/sp/saml2-acs.php/uni-sp",
                headers=aula_saml_headers,
                data=aula_saml_data,
                allow_redirects=False,
            )

            _LOGGER.debug(
                f"Aula SAML POST response status: {aula_response.status_code}"
            )
            _LOGGER.debug(
                f"Aula SAML POST response headers: {dict(aula_response.headers)}"
            )

            # Check for final redirect to Aula login
            if "Location" in aula_response.headers:
                final_aula_url = aula_response.headers["Location"]
                _LOGGER.debug(f"Final Aula redirect URL: {final_aula_url}")

                # Follow the final redirect
                final_aula_response = session.get(final_aula_url)
                _LOGGER.debug(
                    f"Final Aula response status: {final_aula_response.status_code}"
                )
                _LOGGER.debug(
                    f"Final Aula response content length: {len(final_aula_response.text)}"
                )
                _LOGGER.debug(f"Final Aula response URL: {final_aula_response.url}")

                if "login" in final_aula_response.url.lower():
                    # Check if we're back at the login page or if this is a success redirect
                    if (
                        final_aula_response.url
                        == "https://login.aula.dk/auth/login.php?type=unilogin"
                    ):
                        _LOGGER.warning(
                            "Redirected back to login page - authentication may have failed or requires additional steps"
                        )
                    else:
                        _LOGGER.info(
                            "Successfully completed authentication flow - redirected to Aula login page"
                        )
                else:
                    _LOGGER.info(
                        "Authentication completed - redirected to Aula dashboard or other page"
                    )
                    _LOGGER.debug(f"Final URL: {final_aula_response.url}")

                    # If we successfully reached the portal, make API call to get profiles
                    if "portal" in final_aula_response.url.lower():
                        _LOGGER.debug("Making API request to get user profiles...")

                        # Extract CSRF token and other necessary data from the portal page
                        portal_soup = BeautifulSoup(
                            final_aula_response.text, "html.parser"
                        )
                        csrf_token = None

                        # Look for CSRF token in meta tags or script tags
                        csrf_meta = portal_soup.find("meta", {"name": "csrf-token"})
                        if csrf_meta:
                            csrf_token = csrf_meta.get("content")

                        # Prepare headers for the API request
                        api_headers = {
                            "accept": "application/json, text/plain, */*",
                            "accept-encoding": "gzip, deflate, br, zstd",
                            "accept-language": "en-US,en;q=0.9,da;q=0.8",
                            "cache-control": "no-cache",
                            "pragma": "no-cache",
                            "priority": "u=1, i",
                            "referer": "https://www.aula.dk/portal/",
                            "sec-ch-ua": '"Not;A=Brand";v="99", "Google Chrome";v="139", "Chromium";v="139"',
                            "sec-ch-ua-mobile": "?0",
                            "sec-ch-ua-platform": '"Windows"',
                            "sec-fetch-dest": "empty",
                            "sec-fetch-mode": "cors",
                            "sec-fetch-site": "same-origin",
                            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
                        }

                        # Add cookies for the API request
                        api_cookie_header = "; ".join(
                            [
                                f"{c.name}={c.value}"
                                for c in session.cookies
                                if "aula.dk" in c.domain or c.domain == ""
                            ]
                        )
                        if csrf_token:
                            api_cookie_header += f"; Csrfp-Token={csrf_token}"
                        if api_cookie_header:
                            api_headers["cookie"] = api_cookie_header

                        # Make the API request to get profiles
                        api_response = session.get(
                            "https://www.aula.dk/api/v22/?method=profiles.getProfilesByLogin",
                            headers=api_headers,
                        )

                        _LOGGER.debug(
                            f"API response status: {api_response.status_code}"
                        )
                        _LOGGER.debug(
                            f"API response headers: {dict(api_response.headers)}"
                        )

                        if api_response.status_code == 200:
                            try:
                                profiles_data = api_response.json()
                                _LOGGER.info(f"Successfully retrieved profiles data:")
                                _LOGGER.debug(f"Raw JSON response: {profiles_data}")

                                # Try different possible data structures
                                if "data" in profiles_data:
                                    data = profiles_data["data"]
                                    if "profiles" in data:
                                        profiles = data["profiles"]
                                        _LOGGER.info(
                                            f"Number of profiles: {len(profiles)}"
                                        )
                                        for profile in profiles:
                                            _LOGGER.info(
                                                f"Profile: {profile.get('name', 'Unknown')} - ID: {profile.get('id', 'Unknown')}"
                                            )
                                    else:
                                        _LOGGER.debug(
                                            f"Available data keys: {list(data.keys())}"
                                        )
                                else:
                                    _LOGGER.debug(
                                        f"Available top-level keys: {list(profiles_data.keys())}"
                                    )

                            except Exception as e:
                                _LOGGER.error(f"Error parsing profiles JSON: {e}")
                                _LOGGER.debug(
                                    f"Raw response: {api_response.text[:500]}..."
                                )
                        else:
                            _LOGGER.error(
                                f"API request failed with status {api_response.status_code}"
                            )
                            _LOGGER.debug(
                                f"Response content: {api_response.text[:500]}..."
                            )
            else:
                _LOGGER.error("No final redirect found in Aula SAML response")
        else:
            _LOGGER.error(
                "Could not find SAMLResponse or RelayState in after-post-broker response"
            )
    else:
        _LOGGER.error("No SAML form found in after-post-broker response")
else:
    _LOGGER.error("No redirect location found in post-broker-login response")
