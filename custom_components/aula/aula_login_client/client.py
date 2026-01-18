"""
Aula Login Client - Main authentication client for Aula platform with MitID integration.

This module contains the main AulaLoginClient class that handles the complete
authentication flow including OAuth 2.0/OIDC, SAML, and MitID authentication.
"""

import requests
import urllib.parse
import base64
import hashlib
import secrets
import re
import json
import time
import binascii
import uuid
import os
import logging
from pathlib import Path
from typing import Dict, Optional, Tuple, List
from urllib.parse import parse_qs, urlparse, urljoin

from bs4 import BeautifulSoup
from Crypto import Random

from .exceptions import (
    AulaAuthenticationError,
    MitIDError,
    TokenExpiredError,
    APIError,
    ConfigurationError,
    NetworkError,
    SAMLError,
    OAuthError
)

# Import MitID BrowserClient from the local module
try:
    from .mitid_browserclient.BrowserClient import BrowserClient
    MITID_AVAILABLE = True
except ImportError as e:
    MITID_AVAILABLE = False


class AulaLoginClient:
    """
    Main client for Aula platform authentication with MitID integration.

    This class handles the complete OAuth 2.0/OIDC + SAML + MitID authentication flow
    that the Aula mobile application uses, including automated MitID authentication.

    Features:
    - Full authentication flow automation
    - Token management and renewal
    - API access testing
    - Proxy support
    - Comprehensive error handling

    Example:
        client = AulaLoginClient(
            mitid_username="your_username",
            auth_method="APP"
        )

        result = client.authenticate()
        if result['success']:
            tokens = result['tokens']
    """

    def __init__(self, mitid_username: str, mitid_password: Optional[str] = None,
                 auth_method: str = "APP", proxy: Optional[str] = None,
                 timeout: int = 30, debug: bool = False, verbose: bool = False):
        """
        Initialize the Aula login client.

        Args:
            mitid_username: Your MitID username
            mitid_password: Your MitID password (for TOKEN method)
            auth_method: "APP" for MitID app, "TOKEN" for code token
            proxy: Optional SOCKS5 proxy (format: "host:port")
            timeout: Request timeout in seconds
            debug: Enable debug logging
            verbose: Enable verbose output (default: False)

        Raises:
            ConfigurationError: If MitID BrowserClient is not available
        """
        if not MITID_AVAILABLE:
            raise ConfigurationError(
                "MitID BrowserClient not found. Please install: pip install mitid-browserclient"
            )

        self.session = requests.Session()
        self.mitid_username = mitid_username
        self.mitid_password = mitid_password
        self.auth_method = auth_method
        self.timeout = timeout
        self.debug = debug
        self.verbose = verbose

        # Initialize logger for Home Assistant integration
        self.logger = logging.getLogger(__name__)

        # Configure proxy if provided
        if proxy:
            self.session.proxies.update({
                "http": f"socks5://{proxy}",
                "https": f"socks5://{proxy}"
            })

        # Mobile app user agent from MITM flows
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Linux; Android 14; sdk_gphone64_x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Mobile Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Upgrade-Insecure-Requests': '1',
            'sec-ch-ua': '"Google Chrome";v="113", "Chromium";v="113", "Not-A.Brand";v="24"',
            'sec-ch-ua-mobile': '?1',
            'sec-ch-ua-platform': '"Android"'
        })

        # URLs from MITM analysis
        self.auth_base_url = "https://login.aula.dk"
        self.broker_url = "https://broker.unilogin.dk"
        self.app_redirect_uri = "https://app-private.aula.dk"
        self.app_api_base = "https://app-private.aula.dk/api/v22"

        # OAuth configuration from source code
        self.client_id_level_3 = "_99949a54b8b65423862aac1bf629599ed64231607a"
        self.scope = "aula-sensitive"

        # Session state
        self.code_verifier = None
        self.code_challenge = None
        self.state = None
        self.tokens = None
        self.mitid_client = None  # Store MitID client for QR code access

    def log(self, message: str, level: str = "INFO"):
        """Enhanced logging using Home Assistant logging system"""
        # Map string level to logging methods
        level_map = {
            "DEBUG": self.logger.debug,
            "INFO": self.logger.info,
            "WARN": self.logger.warning,
            "WARNING": self.logger.warning,
            "ERROR": self.logger.error
        }

        # Get the appropriate logging method
        log_method = level_map.get(level.upper(), self.logger.info)

        # Apply verbose/debug filtering
        if not self.verbose and level.upper() in ["DEBUG", "INFO"]:
            return
        if not self.debug and level.upper() == "DEBUG":
            return

        # Log the message
        log_method(message)

    def generate_pkce_parameters(self) -> Tuple[str, str]:
        """Generate PKCE parameters for OAuth 2.0"""
        code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('utf-8').rstrip('=')
        challenge = hashlib.sha256(code_verifier.encode('utf-8')).digest()
        code_challenge = base64.urlsafe_b64encode(challenge).decode('utf-8').rstrip('=')
        return code_verifier, code_challenge

    def generate_state(self) -> str:
        """Generate OAuth state parameter"""
        return base64.urlsafe_b64encode(secrets.token_bytes(16)).decode('utf-8').rstrip('=')

    def step1_start_oauth_flow(self) -> str:
        """Step 1: Start OAuth authorization flow"""
        self.log("Starting OAuth 2.0 authorization flow")

        try:
            # Generate PKCE parameters
            self.code_verifier, self.code_challenge = self.generate_pkce_parameters()
            self.state = self.generate_state()

            # Build authorization URL
            auth_params = {
                'response_type': 'code',
                'client_id': self.client_id_level_3,
                'scope': self.scope,
                'redirect_uri': self.app_redirect_uri,
                'state': self.state,
                'code_challenge': self.code_challenge,
                'code_challenge_method': 'S256'
            }

            auth_url = f"{self.auth_base_url}/simplesaml/module.php/oidc/authorize.php"
            full_auth_url = f"{auth_url}?{urllib.parse.urlencode(auth_params)}"

            self.log("Authorization URL generated, now visiting it to start the flow")

            # Actually visit the OAuth authorization URL - this should redirect to SAML
            oauth_response = self.session.get(full_auth_url, allow_redirects=False, timeout=self.timeout)
            self.log(f"OAuth authorization response: {oauth_response.status_code}")

            if oauth_response.status_code in [301, 302, 303, 307, 308]:
                # Follow the redirect to SAML
                redirect_url = oauth_response.headers.get('Location')
                self.log(f"OAuth redirecting to: {redirect_url[:100]}...")
                return redirect_url
            elif oauth_response.status_code == 200:
                # Check if this page contains a SAML form or redirect
                soup = BeautifulSoup(oauth_response.text, 'html.parser')

                # Look for SAML form
                saml_form = soup.find('form')
                if saml_form and saml_form.get('action'):
                    action = saml_form.get('action')
                    self.log(f"Found SAML form with action: {action}")
                    return action

                # Look for meta refresh or JavaScript redirect
                meta_refresh = soup.find('meta', {'http-equiv': 'refresh'})
                if meta_refresh:
                    content = meta_refresh.get('content', '')
                    if 'url=' in content.lower():
                        url = content.split('url=', 1)[1]
                        self.log(f"Found meta refresh redirect: {url}")
                        return url

                raise OAuthError("OAuth authorization endpoint returned 200 but no redirect found")
            else:
                raise OAuthError(f"Unexpected OAuth authorization response: {oauth_response.status_code}")

        except requests.RequestException as e:
            raise NetworkError(f"Network error during OAuth flow: {str(e)}")

    def step3_follow_redirect_chain(self, start_url: str) -> Dict:
        """Step 3: Follow the redirect chain to MitID"""
        self.log("Following redirect chain to MitID")

        current_url = start_url
        redirect_count = 0
        max_redirects = 10

        try:
            while redirect_count < max_redirects:
                response = self.session.get(current_url, allow_redirects=False, timeout=self.timeout)
                self.log(f"Redirect {redirect_count + 1}: {response.status_code} -> {current_url[:80]}...")

                if response.status_code == 200:
                    # We've reached a page that needs interaction
                    soup = BeautifulSoup(response.text, 'html.parser')

                    # Check what kind of page this is
                    if 'broker.unilogin.dk' in response.url:
                        self.log("Reached UniLogin broker - looking for IdP selection")
                        return self._handle_broker_page(soup, response)

                    elif 'mitid.dk' in response.url or 'nemlog-in' in response.url:
                        self.log("Reached MitID page")

                        # Extract verification token
                        token_input = soup.find('input', {'name': '__RequestVerificationToken'})
                        if not token_input:
                            raise SAMLError("Could not find RequestVerificationToken on MitID page")

                        verification_token = token_input['value']
                        self.log(f"Found RequestVerificationToken")

                        return {
                            'verification_token': verification_token,
                            'mitid_url': response.url,
                            'session_cookies': dict(self.session.cookies)
                        }

                    else:
                        raise SAMLError(f"Unexpected page reached: {response.url}")

                elif response.status_code in [301, 302, 303, 307, 308]:
                    if 'Location' not in response.headers:
                        raise SAMLError(f"Redirect response missing Location header")

                    current_url = urljoin(current_url, response.headers['Location'])
                    redirect_count += 1

                else:
                    raise SAMLError(f"Unexpected status code: {response.status_code}")

            raise SAMLError(f"Too many redirects ({max_redirects})")

        except requests.RequestException as e:
            raise NetworkError(f"Network error during redirect chain: {str(e)}")

    def _handle_broker_page(self, soup, response) -> Dict:
        """Handle the broker page for IdP selection"""
        # Look for MitID/NemLogin selection form or button
        forms = soup.find_all('form')
        self.log(f"Found {len(forms)} forms on the page")

        # Try standard form submission for NemLogin/MitID
        main_form = soup.find('form')
        if main_form:
            action = main_form.get('action', '')
            if action:
                self.log(f"Submitting form to: {action}")

                # Common patterns for MitID selection
                form_data = {}

                # Try various common parameter names for IdP selection
                idp_selectors = ['selectedIdp', 'idp', 'authMethod', 'provider']
                idp_values = ['nemlogin3', 'mitid', 'MitID', 'nemlogin']

                # Look for existing hidden inputs
                for inp in main_form.find_all('input'):
                    name = inp.get('name')
                    value = inp.get('value', '')
                    if name:
                        form_data[name] = value

                # Try to set IdP selection
                for selector in idp_selectors:
                    for value in idp_values:
                        test_data = form_data.copy()
                        test_data[selector] = value

                        self.log(f"Trying form submission with {selector}={value}")

                        post_response = self.session.post(action, data=test_data, allow_redirects=False, timeout=self.timeout)
                        self.log(f"Form submission result: {post_response.status_code}")

                        if post_response.status_code in [301, 302, 303, 307, 308]:
                            if 'Location' in post_response.headers:
                                current_url = post_response.headers['Location']
                                self.log(f"Form submission redirected to: {current_url[:100]}...")
                                # Continue following redirects from this new URL
                                return self.step3_follow_redirect_chain(current_url)

                raise SAMLError("Could not find working IdP selection method")

        raise SAMLError("No usable form found on broker page")

    def step4_mitid_authentication(self, verification_token: str) -> str:
        """Step 4: Perform MitID authentication"""
        self.log(f"Starting MitID authentication (method: {self.auth_method})")

        try:
            # Initialize MitID authentication
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
                "x-requested-with": "XMLHttpRequest"
            }

            post_data = {
                "__RequestVerificationToken": verification_token
            }

            resp_init = self.session.post(post_url, headers=post_headers, data=post_data, timeout=self.timeout)
            resp_init_json = resp_init.json()

            if isinstance(resp_init_json, str):
                resp_init_json = json.loads(resp_init_json)

            aux_value = resp_init_json.get("Aux")
            if not aux_value:
                raise MitIDError("No Aux value in MitID initialization response")

            aux = json.loads(base64.b64decode(aux_value).decode())

            # Use MitID BrowserClient for authentication
            authorization_code = self._get_mitid_authentication_code(aux)
            self.log(f"MitID authentication code obtained")

            # Clear MitID client to prevent QR codes from showing up again in UI
            self.mitid_client = None

            return authorization_code

        except requests.RequestException as e:
            raise NetworkError(f"Network error during MitID authentication: {str(e)}")
        except (json.JSONDecodeError, KeyError) as e:
            raise MitIDError(f"Invalid MitID response format: {str(e)}")

    def _get_mitid_authentication_code(self, aux: Dict) -> str:
        """Use MitID BrowserClient to get authentication code"""
        try:
            client_hash = binascii.hexlify(base64.b64decode(aux["coreClient"]["checksum"])).decode('ascii')
            authentication_session_id = aux["parameters"]["authenticationSessionId"]

            self.mitid_client = BrowserClient(client_hash, authentication_session_id, self.session)
            available_authenticators = self.mitid_client.identify_as_user_and_get_available_authenticators(self.mitid_username)

            self.log(f"Available authenticators: {available_authenticators}")

            if self.auth_method == "TOKEN" and "TOKEN" in available_authenticators:
                if not self.mitid_password:
                    self.mitid_password = input("Please input your MitID password: ").strip()
                token_digits = input("Please input the 6 digits from your code token: ").strip()
                self.mitid_client.authenticate_with_token(token_digits)
                self.mitid_client.authenticate_with_password(self.mitid_password)
            elif self.auth_method == "APP" and "APP" in available_authenticators:
                self.mitid_client.authenticate_with_app()
            else:
                raise MitIDError(f"Authentication method {self.auth_method} not available")

            authorization_code = self.mitid_client.finalize_authentication_and_get_authorization_code()
            return authorization_code

        except Exception as e:
            raise MitIDError(f"MitID authentication failed: {str(e)}")

    def step5_complete_mitid_flow(self, verification_token: str, authorization_code: str) -> Dict:
        """Step 5: Complete MitID authentication and get SAML response"""
        self.log("Completing MitID authentication flow")

        try:
            session_storage_active_session_uuid = self.session.cookies.get('SessionUuid', '')
            session_storage_active_challenge = self.session.cookies.get('Challenge', '')

            params = {
                "__RequestVerificationToken": verification_token,
                "NewCulture": "",
                "MitIDUseConfirmed": "True",
                "MitIDAuthCode": authorization_code,
                "MitIDAuthenticationCancelled": "",
                "MitIDCoreClientError": "",
                "SessionStorageActiveSessionUuid": session_storage_active_session_uuid,
                "SessionStorageActiveChallenge": session_storage_active_challenge
            }

            self.log(f"Submitting MitID completion data to: https://nemlog-in.mitid.dk/login/mitid")
            request = self.session.post("https://nemlog-in.mitid.dk/login/mitid", data=params, timeout=self.timeout)

            self.log(f"MitID completion response: {request.status_code}")
            self.log(f"Final URL: {request.url}")

            soup = BeautifulSoup(request.text, features="html.parser")

            # Handle multiple identity options if present
            if request.url == 'https://nemlog-in.mitid.dk/loginoption':
                self.log("Multiple identity options detected, choosing...")
                request, soup = self._choose_between_multiple_identities(request, soup)
                self.log(f"After identity choice: {request.status_code} -> {request.url}")

            # Extract SAML response
            relay_state_input = soup.find('input', {'name': 'RelayState'})
            saml_response_input = soup.find('input', {'name': 'SAMLResponse'})

            if not relay_state_input:
                raise SAMLError("Could not find RelayState in MitID completion response")

            if not saml_response_input:
                raise SAMLError("Could not find SAMLResponse in MitID completion response")

            relay_state = relay_state_input.get('value')
            saml_response = saml_response_input.get('value')

            self.log(f" SAML data extracted successfully")

            return {
                'relay_state': relay_state,
                'saml_response': saml_response,
                'completion_url': request.url
            }

        except requests.RequestException as e:
            raise NetworkError(f"Network error during MitID completion: {str(e)}")

    def _choose_between_multiple_identities(self, request, soup):
        """Handle multiple identity selection"""
        data = {}
        for soup_input in soup.form.select("input"):
            try:
                data[soup_input["name"]] = soup_input["value"]
            except:
                data[soup_input["name"]] = ""

        # Update SessionStorage values from cookies (they might have changed)
        session_uuid = self.session.cookies.get('SessionUuid', '')
        challenge = self.session.cookies.get('Challenge', '')
        if session_uuid:
            data["SessionStorageActiveSessionUuid"] = session_uuid
        if challenge:
            data["SessionStorageActiveChallenge"] = challenge

        login_options = soup.select("div.list-link-box")
        identities = []
        identity_names = []
        for i, login_option in enumerate(login_options):
            identity_name = login_option.select_one("div.list-link-text").string
            identities.append(i+1)
            identity_names.append(identity_name)

        # Store the available identities for external handling
        self.available_identities = identity_names

        # If identity_selector callback is provided, use it; otherwise use input()
        if hasattr(self, 'identity_selector') and self.identity_selector:
            identity = self.identity_selector(identity_names)
        else:
            self.log('You can choose between different identities:', 'INFO')
            for i, name in enumerate(identity_names):
                self.log(f'{i+1}: {name}', 'INFO')
            identity = input("Enter the identity you want to use: ").strip()

        if int(identity) in identities:
            selected_login_option = login_options[int(identity)-1]
            selected_link = selected_login_option.a
            selected_option = selected_link["data-loginoptions"]
            data["ChosenOptionJson"] = selected_option
        else:
            raise MitIDError("Identity not in list of identities")

        # Need to follow redirects and use proper headers
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": request.url,
        }
        request = self.session.post(request.url, data=data, headers=headers, timeout=self.timeout, allow_redirects=True)
        soup = BeautifulSoup(request.text, features="html.parser")
        return request, soup

    def step6_saml_broker_flow(self, saml_data: Dict) -> Dict:
        """Step 6: Complete SAML broker authentication"""
        self.log("Processing SAML broker flow")

        try:
            # Post SAML response to broker
            saml_post_headers = {
                "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
                "accept-encoding": "gzip, deflate, br, zstd",
                "accept-language": "en-US,en;q=0.9,da;q=0.8",
                "cache-control": "max-age=0",
                "content-type": "application/x-www-form-urlencoded",
                "origin": "https://nemlog-in.mitid.dk",
                "referer": "https://nemlog-in.mitid.dk/login/mitid",
                "sec-fetch-dest": "document",
                "sec-fetch-mode": "navigate",
                "sec-fetch-site": "cross-site",
                "sec-fetch-user": "?1",
                "upgrade-insecure-requests": "1"
            }

            params = {
                "RelayState": saml_data['relay_state'],
                "SAMLResponse": saml_data['saml_response']
            }

            # Add cookies to headers
            cookie_header = '; '.join([f'{c.name}={c.value}' for c in self.session.cookies])
            if cookie_header:
                saml_post_headers["cookie"] = cookie_header

            # Post to broker endpoint
            broker_response = self.session.post(
                "https://broker.unilogin.dk/auth/realms/broker/broker/nemlogin3/endpoint",
                data=params,
                headers=saml_post_headers,
                allow_redirects=False,
                timeout=self.timeout
            )

            if 'Location' not in broker_response.headers:
                raise SAMLError("No redirect from broker endpoint")

            # Follow redirect chain through broker
            action_url = broker_response.headers['Location']
            self.log(f"Broker redirect URL: {action_url}")
            final_request = self.session.get(action_url, timeout=self.timeout)
            self.log(f"Final request URL: {final_request.url}")
            self.log(f"Final request status: {final_request.status_code}")

            return self._process_broker_response(final_request)

        except requests.RequestException as e:
            raise NetworkError(f"Network error during SAML broker flow: {str(e)}")

    def _process_broker_response(self, response) -> Dict:
        """Process broker response and extract session parameters"""
        soup = BeautifulSoup(response.text, 'html.parser')

        # Extract session parameters from URL or form
        parsed_url = urlparse(response.url)
        query_params = parse_qs(parsed_url.query)

        session_code = query_params.get('session_code', [''])[0]
        execution = query_params.get('execution', [''])[0]
        client_id = query_params.get('client_id', [''])[0]
        tab_id = query_params.get('tab_id', [''])[0]

        # Fallback to form extraction if not in URL
        if not session_code or not execution:
            form = soup.find('form')
            if form:
                form_action = form.get('action', '')
                if form_action:
                    parsed_form_url = urlparse(form_action)
                    form_query_params = parse_qs(parsed_form_url.query)
                    session_code = form_query_params.get('session_code', [''])[0]
                    execution = form_query_params.get('execution', [''])[0]
                    client_id = form_query_params.get('client_id', [''])[0]
                    tab_id = form_query_params.get('tab_id', [''])[0]

        # Complete post-broker-login
        self.log(f"Broker response URL for param extraction: {response.url}")
        self.log(f"Broker params - session_code: {session_code}, execution: {execution}, client_id: {client_id}, tab_id: {tab_id}")

        # Extract form data to submit
        form = soup.find('form')
        form_data = {}
        if form:
            form_action = form.get('action', '')
            self.log(f"Found form action: {form_action}")
            inputs = form.find_all('input')
            for inp in inputs:
                name = inp.get('name')
                value = inp.get('value', '')
                if name:
                    form_data[name] = value
                self.log(f"  Form input: name={name}, type={inp.get('type')}, value={value[:50] if value else 'empty'}")

            # Use the form action URL if available (it has all the proper params)
            if form_action and form_action.startswith('http'):
                post_broker_url = form_action
            elif form_action:
                post_broker_url = f"https://broker.unilogin.dk{form_action}"
            else:
                post_broker_url = f"https://broker.unilogin.dk/auth/realms/broker/login-actions/post-broker-login?session_code={session_code}&execution={execution}&client_id={client_id}&tab_id={tab_id}"
        else:
            self.log("No form found in broker response")
            post_broker_url = f"https://broker.unilogin.dk/auth/realms/broker/login-actions/post-broker-login?session_code={session_code}&execution={execution}&client_id={client_id}&tab_id={tab_id}"

        if not session_code or not execution:
            self.log(f"Warning: Missing broker params. Response URL: {response.url}")
            self.log(f"Response body (first 1000 chars): {response.text[:1000]}")

        self.log(f"Post-broker URL: {post_broker_url}")
        self.log(f"Post-broker form data: {form_data}")

        post_broker_headers = {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "content-type": "application/x-www-form-urlencoded",
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "same-origin",
            "upgrade-insecure-requests": "1"
        }

        # Add cookies
        cookie_header = '; '.join([f'{c.name}={c.value}' for c in self.session.cookies])
        if cookie_header:
            post_broker_headers["cookie"] = cookie_header

        post_broker_response = self.session.post(
            post_broker_url,
            headers=post_broker_headers,
            data=form_data,
            allow_redirects=False,
            timeout=self.timeout
        )

        self.log(f"Post-broker response status: {post_broker_response.status_code}")
        self.log(f"Post-broker response headers: {dict(post_broker_response.headers)}")
        if post_broker_response.status_code != 302:
            self.log(f"Post-broker response body: {post_broker_response.text}")

        if 'Location' not in post_broker_response.headers:
            raise SAMLError(f"No redirect from post-broker-login (status={post_broker_response.status_code})")

        # Follow final redirect
        after_post_broker_url = post_broker_response.headers['Location']
        after_response = self.session.get(after_post_broker_url, timeout=self.timeout)

        # Extract final SAML response for Aula
        after_soup = BeautifulSoup(after_response.text, 'html.parser')

        self.log(f"Final broker response URL: {after_response.url}")
        self.log(f"Final broker response status: {after_response.status_code}")

        saml_form = after_soup.find('form')

        if not saml_form:
            raise SAMLError("No SAML form found in broker response")

        self.log(f" Found SAML form with action: {saml_form.get('action', 'N/A')}")

        saml_response_input = saml_form.find('input', {'name': 'SAMLResponse'})
        relay_state_input = saml_form.find('input', {'name': 'RelayState'})

        if not saml_response_input:
            raise SAMLError("Could not find SAMLResponse - this is critical")

        # RelayState might be optional in some flows
        relay_state_value = relay_state_input.get('value') if relay_state_input else ""

        if not relay_state_input:
            self.log("  RelayState not found - this might be OK for Level 3 auth flow")

        return {
            'final_saml_response': saml_response_input.get('value'),
            'final_relay_state': relay_state_value,
            'form_action': saml_form.get('action', '')
        }

    def step7_complete_aula_login(self, saml_data: Dict) -> str:
        """Step 7: Complete Aula login with SAML response"""
        self.log("Completing Aula login with SAML response")

        try:
            aula_saml_headers = {
                "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                "content-type": "application/x-www-form-urlencoded",
                "origin": "null",
                "sec-fetch-dest": "document",
                "sec-fetch-mode": "navigate",
                "sec-fetch-site": "cross-site",
                "upgrade-insecure-requests": "1"
            }

            # Add Aula cookies
            aula_cookie_header = '; '.join([f'{c.name}={c.value}' for c in self.session.cookies
                                           if 'aula.dk' in c.domain or c.domain == ''])
            if aula_cookie_header:
                aula_saml_headers["cookie"] = aula_cookie_header

            aula_saml_data = {
                "SAMLResponse": saml_data['final_saml_response'],
                "RelayState": saml_data['final_relay_state']
            }

            # Use the form action from the broker response instead of hardcoding
            saml_endpoint = saml_data.get('form_action', 'https://login.aula.dk/simplesaml/module.php/saml/sp/saml2-acs.php/uni-sp')
            self.log(f"Submitting SAML response to: {saml_endpoint}")

            # Post to Aula SAML endpoint
            aula_response = self.session.post(
                saml_endpoint,
                headers=aula_saml_headers,
                data=aula_saml_data,
                allow_redirects=False,
                timeout=self.timeout
            )

            self.log(f"SAML response status: {aula_response.status_code}")

            if 'Location' not in aula_response.headers:
                raise OAuthError("No redirect from Aula SAML endpoint")

            # Follow the redirect chain manually to capture all steps
            return self._follow_oauth_callback_redirects(aula_response.headers['Location'])

        except requests.RequestException as e:
            raise NetworkError(f"Network error during Aula login completion: {str(e)}")

    def _follow_oauth_callback_redirects(self, start_url: str) -> str:
        """Follow redirects to find the OAuth callback URL"""
        redirect_url = start_url
        redirect_count = 0
        max_redirects = 10

        while redirect_count < max_redirects:
            redirect_count += 1
            self.log(f"Following redirect #{redirect_count}: {redirect_url[:100]}...")

            redirect_response = self.session.get(redirect_url, allow_redirects=False, timeout=self.timeout)
            self.log(f"Redirect response status: {redirect_response.status_code}")

            # Check if this is the OAuth callback we're looking for
            if self.app_redirect_uri in redirect_response.url and 'code=' in redirect_response.url:
                self.log(f" Found OAuth callback URL: {redirect_response.url}")
                return redirect_response.url

            # Check if this is the OAuth callback in Location header
            if 'Location' in redirect_response.headers:
                location = redirect_response.headers['Location']
                if self.app_redirect_uri in location and 'code=' in location:
                    self.log(f" Found OAuth callback in Location header: {location}")
                    return location

            # If it's a 200 response, check the final URL
            if redirect_response.status_code == 200:
                final_url = redirect_response.url
                self.log(f"Final destination (200): {final_url}")

                # Check if we got an OAuth callback URL
                if self.app_redirect_uri in final_url and 'code=' in final_url:
                    return final_url
                else:
                    raise OAuthError(f"Did not receive OAuth callback URL. Final URL: {final_url}")

            # If it's a redirect, follow it
            elif redirect_response.status_code in [301, 302, 303, 307, 308]:
                if 'Location' not in redirect_response.headers:
                    raise OAuthError(f"Redirect response missing Location header at step {redirect_count}")

                redirect_url = redirect_response.headers['Location']

                # Handle relative URLs
                if redirect_url.startswith('/'):
                    base_url = f"{urlparse(redirect_response.url).scheme}://{urlparse(redirect_response.url).netloc}"
                    redirect_url = urljoin(base_url, redirect_url)
            else:
                raise OAuthError(f"Unexpected status code {redirect_response.status_code} at redirect step {redirect_count}")

        raise OAuthError(f"Too many redirects ({max_redirects}) without finding OAuth callback")

    def step8_exchange_oauth_code(self, callback_url: str) -> Dict:
        """Step 8: Exchange OAuth authorization code for tokens"""
        self.log("Exchanging OAuth authorization code for tokens")

        try:
            # Parse callback URL
            parsed_url = urlparse(callback_url)
            query_params = parse_qs(parsed_url.query)

            if 'code' not in query_params:
                raise OAuthError("No authorization code in callback URL")

            auth_code = query_params['code'][0]

            # Verify state parameter
            if 'state' in query_params:
                returned_state = query_params['state'][0]
                if returned_state != self.state:
                    raise OAuthError("State parameter mismatch")

            # Exchange code for tokens
            token_url = f"{self.auth_base_url}/simplesaml/module.php/oidc/token.php"

            token_data = {
                'grant_type': 'authorization_code',
                'code': auth_code,
                'client_id': self.client_id_level_3,
                'redirect_uri': self.app_redirect_uri,
                'code_verifier': self.code_verifier
            }

            headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': 'application/json'
            }

            response = self.session.post(token_url, data=token_data, headers=headers, timeout=self.timeout)

            if response.status_code != 200:
                raise OAuthError(f"Token exchange failed: {response.status_code} - {response.text}")

            tokens = response.json()

            # Calculate expires_at timestamp for persistence
            if 'expires_in' in tokens:
                tokens['expires_at'] = time.time() + tokens['expires_in']

            self.tokens = tokens

            expires_in = tokens.get('expires_in', 0)
            if expires_in:
                hours = int(expires_in // 3600)
                minutes = int((expires_in % 3600) // 60)
                self.log(f"Token exchange successful! Token lifetime: {hours}h {minutes}m ({expires_in}s)")
            else:
                self.log("Token exchange successful! (no expiration info)")

            return tokens

        except requests.RequestException as e:
            raise NetworkError(f"Network error during token exchange: {str(e)}")
        except json.JSONDecodeError as e:
            raise OAuthError(f"Invalid token response format: {str(e)}")

    def step9_test_api_access(self) -> Dict:
        """Step 9: Test API access with obtained tokens"""
        self.log("Testing Aula API access")

        if not self.tokens or 'access_token' not in self.tokens:
            raise APIError("No access token available")

        try:
            # Based on HAR file analysis - actual v22 API endpoints with correct parameters
            v22_api_base = "https://www.aula.dk/api/v22/"

            # Generate a device ID similar to what the app uses
            device_id = f"Droid-private-{str(uuid.uuid4())}"

            test_endpoints = [
                # Core configuration endpoints (no auth needed)
                ("centralConfiguration.getLoginImportantInformation", "GET", {"platform": "android-private"}),

                # Profile endpoints (main user data) - these need access_token
                ("profiles.getProfileTypesByLogin", "GET", {"access_token": self.tokens["access_token"]}),
                ("profiles.getprofilesbylogin", "GET", {"portalRoles[]": "guardian", "access_token": self.tokens["access_token"]}),
            ]

            results = {}
            api_headers = {
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'User-Agent': self.session.headers['User-Agent']
            }

            for method_name, http_method, params in test_endpoints:
                # Build URL with method parameter
                url_params = {"method": method_name}
                url_params.update(params)

                # Convert params to query string
                param_string = "&".join([f"{k}={v}" for k, v in url_params.items()])
                url = f"{v22_api_base}?{param_string}"

                try:
                    self.log(f"Testing v22 API method: {method_name}")

                    if http_method == "GET":
                        response = self.session.get(url, headers=api_headers, timeout=self.timeout)
                    elif http_method == "POST":
                        response = self.session.post(url, headers=api_headers, json=params.get('data', {}), timeout=self.timeout)
                    else:
                        continue

                    results[method_name] = {
                        'status_code': response.status_code,
                        'success': response.status_code == 200,
                        'url': url,
                        'method': http_method
                    }

                    if response.status_code == 200:
                        try:
                            data = response.json()
                            results[method_name]['data'] = data
                            self.log(f" {method_name}: Success - response received")
                        except:
                            results[method_name]['data'] = response.text[:200]
                            self.log(f" {method_name}: Success - non-JSON response")
                    else:
                        results[method_name]['error'] = f"HTTP {response.status_code}"
                        self.log(f"  {method_name}: {response.status_code}")

                except Exception as e:
                    self.log(f" {method_name}: Exception - {str(e)}")
                    results[method_name] = {
                        'success': False,
                        'error': str(e),
                        'url': url
                    }

            # Check if any endpoint worked
            successful_endpoints = [ep for ep, result in results.items() if result.get('success')]

            if successful_endpoints:
                self.log(f"✅ API access working! Successful endpoints: {successful_endpoints}")
                return {
                    'success': True,
                    'results': results,
                    'working_endpoints': successful_endpoints
                }
            else:
                self.log("  No API endpoints worked, but authentication was successful")
                return {
                    'success': False,
                    'results': results,
                    'message': 'Authentication successful but API access limited'
                }

        except requests.RequestException as e:
            raise NetworkError(f"Network error during API testing: {str(e)}")

    def check_token_expiration(self) -> Dict:
        """Check if the access token is about to expire"""
        if not self.tokens or 'access_token' not in self.tokens:
            return {'valid': False, 'reason': 'No access token available'}

        try:
            # Decode JWT token to check expiration
            token_parts = self.tokens['access_token'].split('.')
            if len(token_parts) >= 2:
                payload = token_parts[1]
                padding = 4 - (len(payload) % 4)
                if padding != 4:
                    payload += '=' * padding
                decoded = base64.urlsafe_b64decode(payload)
                token_data = json.loads(decoded)

                exp_timestamp = token_data.get('exp')
                if exp_timestamp:
                    current_time = time.time()
                    expires_in = exp_timestamp - current_time

                    # Consider token expired if less than 5 minutes remaining
                    if expires_in < 300:  # 5 minutes
                        return {
                            'valid': False,
                            'reason': f'Token expires in {int(expires_in)} seconds',
                            'expires_in': expires_in
                        }
                    else:
                        return {
                            'valid': True,
                            'expires_in': expires_in,
                            'expires_at': exp_timestamp
                        }

        except Exception as e:
            self.log(f"Error checking token expiration: {str(e)}")

        # If we can't decode the token, assume it's invalid
        return {'valid': False, 'reason': 'Unable to decode token'}

    def renew_access_token(self) -> bool:
        """Renew the access token using the refresh token"""
        if not self.tokens or 'refresh_token' not in self.tokens:
            self.log("No refresh token available for renewal", "ERROR")
            return False

        try:
            self.log("Attempting to renew access token using refresh token")

            token_url = f"{self.auth_base_url}/simplesaml/module.php/oidc/token.php"

            token_data = {
                'grant_type': 'refresh_token',
                'refresh_token': self.tokens['refresh_token'],
                'client_id': self.client_id_level_3
            }

            token_headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': 'application/json',
                'User-Agent': self.session.headers['User-Agent']
            }

            response = self.session.post(token_url, data=token_data, headers=token_headers, timeout=self.timeout)

            if response.status_code == 200:
                token_response = response.json()

                # Update tokens
                self.tokens['access_token'] = token_response['access_token']
                if 'refresh_token' in token_response:
                    self.tokens['refresh_token'] = token_response['refresh_token']
                if 'expires_in' in token_response:
                    self.tokens['expires_in'] = token_response['expires_in']
                    # Calculate expires_at timestamp for persistence
                    self.tokens['expires_at'] = time.time() + token_response['expires_in']

                expires_in = token_response.get('expires_in', 0)
                if expires_in:
                    hours = int(expires_in // 3600)
                    minutes = int((expires_in % 3600) // 60)
                    self.log(f"Token renewed successfully! New token lifetime: {hours}h {minutes}m ({expires_in}s)")
                else:
                    self.log("Token renewed successfully!")
                return True
            else:
                self.log(f" Token renewal failed: {response.status_code} - {response.text}", "ERROR")
                return False

        except Exception as e:
            self.log(f" Token renewal exception: {str(e)}", "ERROR")
            return False

    def test_token_validity(self) -> bool:
        """Test if the current token is valid by making a simple API call"""
        try:
            # Test with a simple API call
            test_url = f"https://www.aula.dk/api/v22/?method=profiles.getProfileContext&portalrole=guardian"

            headers = {
                'Accept': 'application/json',
                'User-Agent': self.session.headers['User-Agent']
            }

            # Add access token if we have one
            if self.tokens and 'access_token' in self.tokens:
                test_url += f"&access_token={self.tokens['access_token']}"

                response = self.session.get(test_url, headers=headers, timeout=10)

            if response.status_code == 200:
                self.log("Token validation successful")
                return True
            elif response.status_code in [401, 403, 500]:
                self.log(f"Token validation failed: {response.status_code} - Token invalid/expired")
                return False
            else:
                self.log(f"Token validation uncertain: {response.status_code}")
                return True  # Assume valid for non-auth errors

        except Exception as e:
            self.log(f"Token validation exception: {str(e)}")
            return False

    def authenticate(self) -> Dict:
        """
        Execute the complete authentication flow.

        Returns:
            Dict containing success status, tokens, and profile data

        Raises:
            AulaAuthenticationError: If authentication fails at any step
        """
        self.log("=" * 60)
        self.log("STARTING INTEGRATED AULA LOGIN FLOW")
        self.log("=" * 60)

        try:
            # Step 1: Start OAuth flow (this will redirect to SAML)
            saml_redirect_url = self.step1_start_oauth_flow()

            # Step 2: Follow the OAuth→SAML redirect chain to MitID
            mitid_data = self.step3_follow_redirect_chain(saml_redirect_url)

            # Step 3: MitID authentication
            auth_code = self.step4_mitid_authentication(mitid_data['verification_token'])

            # Step 4: Complete MitID flow
            saml_response_data = self.step5_complete_mitid_flow(mitid_data['verification_token'], auth_code)

            # Step 5: SAML broker flow
            broker_data = self.step6_saml_broker_flow(saml_response_data)

            # Step 6: Complete Aula login (this should now redirect back to OAuth)
            callback_url = self.step7_complete_aula_login(broker_data)

            # Step 7: Exchange OAuth code
            tokens = self.step8_exchange_oauth_code(callback_url)

            # Step 8: Test API access
            profile_data = self.step9_test_api_access()

            self.log("=" * 60)
            self.log("AUTHENTICATION FLOW COMPLETED SUCCESSFULLY!")
            self.log("=" * 60)

            return {
                'success': True,
                'tokens': tokens,
                'profile_data': profile_data
            }

        except Exception as e:
            self.log(f"Authentication flow failed: {str(e)}", "ERROR")
            # Re-raise with more specific error type if possible
            if isinstance(e, (AulaAuthenticationError, NetworkError)):
                raise
            else:
                raise AulaAuthenticationError(f"Authentication failed: {str(e)}")

    def get_mitid_client(self):
        """Get the MitID BrowserClient if available."""
        return getattr(self, 'mitid_client', None)

    def get_qr_codes_svg(self):
        """Get QR codes as SVG strings for UI display."""
        if not self.mitid_client:
            return None

        qr_codes = self.mitid_client.get_current_qr_codes()
        if not qr_codes:
            return None

        qr1, qr2 = qr_codes
        return (self._qr_to_svg(qr1), self._qr_to_svg(qr2))

    def _qr_to_svg(self, qr_code):
        """Convert QR code object to SVG string."""
        matrix = qr_code.get_matrix()
        size = len(matrix)
        cell_size = 10

        svg_parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{size * cell_size}" height="{size * cell_size}" '
            f'viewBox="0 0 {size} {size}">',
            '<rect width="100%" height="100%" fill="white"/>',
        ]

        for y, row in enumerate(matrix):
            for x, cell in enumerate(row):
                if cell:
                    svg_parts.append(
                        f'<rect x="{x}" y="{y}" width="1" height="1" fill="black"/>'
                    )

        svg_parts.append('</svg>')
        return ''.join(svg_parts)

    def login_and_save_token(self, token_file_path: str = "tokens.json") -> Dict:
        """
        Perform login and save token information to a JSON file.

        Args:
            token_file_path: Path where to save the token JSON file (default: "tokens.json")

        Returns:
            Dict containing authentication result and token information

        Raises:
            AulaAuthenticationError: If login fails
            IOError: If unable to save token file
        """
        self.log(f"Starting login flow with token save to: {token_file_path}")

        try:
            # Perform authentication
            auth_result = self.authenticate()

            if not auth_result.get('success'):
                raise AulaAuthenticationError("Authentication flow did not succeed")

            # Prepare token data with metadata
            token_data = {
                'timestamp': time.time(),
                'created_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                'username': self.mitid_username,
                'auth_method': self.auth_method,
                'tokens': auth_result['tokens'],
                'client_info': {
                    'client_id': self.client_id_level_3,
                    'scope': self.scope
                }
            }

            # Add expiration information if available
            if 'expires_in' in auth_result['tokens']:
                expires_at = time.time() + auth_result['tokens']['expires_in']
                token_data['expires_at'] = expires_at
                token_data['expires_at_readable'] = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(expires_at))

            # Save to file
            token_path = Path(token_file_path)
            token_path.parent.mkdir(parents=True, exist_ok=True)

            with open(token_path, 'w') as f:
                json.dump(token_data, f, indent=2)

            self.log(f" Token data saved successfully to: {token_file_path}")

            return {
                'success': True,
                'token_file': str(token_path),
                'tokens': auth_result['tokens'],
                'expires_at': token_data.get('expires_at'),
                'profile_data': auth_result.get('profile_data')
            }

        except Exception as e:
            self.log(f"Login and save failed: {str(e)}", "ERROR")
            raise

    def get_valid_token(self, token_file_path: str = "tokens.json") -> Optional[Dict]:
        """
        Check for existing token, validate it, and return it if valid.
        If token is expired but refresh token is available, attempt to renew.
        If renewal fails or no valid token exists, return None.

        Args:
            token_file_path: Path to the token JSON file (default: "tokens.json")

        Returns:
            Dict containing valid token information, or None if no valid token available
        """
        self.log(f"Checking for valid token in: {token_file_path}")

        token_path = Path(token_file_path)

        # Check if token file exists
        if not token_path.exists():
            self.log(f"Token file does not exist: {token_file_path}")
            return None

        try:
            # Load token data
            with open(token_path, 'r') as f:
                token_data = json.load(f)

            # Validate token data structure
            if not isinstance(token_data, dict) or 'tokens' not in token_data:
                self.log("Invalid token file format", "WARN")
                return None

            tokens = token_data['tokens']
            if not tokens.get('access_token'):
                self.log("No access token found in file", "WARN")
                return None

            # Set the tokens in the client
            self.tokens = tokens

            # Check if token is expired using file metadata
            if 'expires_at' in token_data:
                current_time = time.time()
                if current_time >= token_data['expires_at']:
                    self.log("Token expired according to file metadata")
                    return self._attempt_token_renewal(token_data, token_path)

            # Check token validity by examining JWT payload
            token_check = self.check_token_expiration()

            if token_check.get('valid'):
                expires_in = token_check.get('expires_in', 0)
                self.log(f" Token is valid! Expires in {int(expires_in)} seconds")

                # Test actual token validity with API call
                if self.test_token_validity():
                    return {
                        'success': True,
                        'tokens': tokens,
                        'source': 'cached',
                        'expires_in': expires_in,
                        'expires_at': token_check.get('expires_at'),
                        'cached_data': token_data
                    }
                else:
                    self.log("Token failed API validation test")
                    return self._attempt_token_renewal(token_data, token_path)
            else:
                self.log(f"Token not valid: {token_check.get('reason', 'Unknown reason')}")
                return self._attempt_token_renewal(token_data, token_path)

        except (json.JSONDecodeError, IOError) as e:
            self.log(f"Error reading token file: {str(e)}", "ERROR")
            return None
        except Exception as e:
            self.log(f"Unexpected error during token validation: {str(e)}", "ERROR")
            return None

    def _attempt_token_renewal(self, token_data: Dict, token_path: Path) -> Optional[Dict]:
        """
        Attempt to renew the token using refresh token.

        Args:
            token_data: Original token data from file
            token_path: Path to token file

        Returns:
            Dict with renewed token info, or None if renewal fails
        """
        self.log("Attempting token renewal...")

        if not token_data['tokens'].get('refresh_token'):
            self.log("No refresh token available for renewal")
            return None

        try:
            # Attempt renewal
            if self.renew_access_token():
                self.log("Token renewed successfully")

                # Update token data with new tokens
                token_data['tokens'] = self.tokens
                token_data['renewed_at'] = time.time()
                token_data['renewed_at_readable'] = time.strftime('%Y-%m-%d %H:%M:%S')

                # Update expiration info
                if 'expires_in' in self.tokens:
                    expires_at = time.time() + self.tokens['expires_in']
                    token_data['expires_at'] = expires_at
                    token_data['expires_at_readable'] = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(expires_at))

                # Save updated tokens
                with open(token_path, 'w') as f:
                    json.dump(token_data, f, indent=2)

                self.log(f"Updated token file saved: {token_path}")

                return {
                    'success': True,
                    'tokens': self.tokens,
                    'source': 'renewed',
                    'expires_in': self.tokens.get('expires_in'),
                    'expires_at': token_data.get('expires_at'),
                    'cached_data': token_data
                }
            else:
                self.log("Token renewal failed")
                return None

        except Exception as e:
            self.log(f"Token renewal failed with exception: {str(e)}", "ERROR")
            return None

    @property
    def access_token(self) -> Optional[str]:
        """Get the current access token"""
        return self.tokens.get('access_token') if self.tokens else None

    @property
    def refresh_token(self) -> Optional[str]:
        """Get the current refresh token"""
        return self.tokens.get('refresh_token') if self.tokens else None

    def is_authenticated(self) -> bool:
        """Check if the client has valid tokens"""
        return bool(self.tokens and self.tokens.get('access_token'))

    def get_current_qr_codes(self):
        """Get current QR codes from MitID client if available"""
        if self.mitid_client and hasattr(self.mitid_client, 'get_current_qr_codes'):
            return self.mitid_client.get_current_qr_codes()
        return None

    def get_valid_access_token(self, token_file_path: str = "tokens.json") -> Dict:
        """
        Simple function to get a valid access token.

        This function will:
        1. Check if the current token is still valid
        2. If expired, try to refresh it using the token from the JSON file
        3. Test the token by making an actual API call
        4. If token test fails, return a "token not valid" error

        Args:
            token_file_path: Path to the token JSON file (default: "tokens.json")

        Returns:
            Dict with structure:
            - success: bool - Whether a valid token was obtained
            - access_token: str - The valid access token (if success=True)
            - error: str - Error message (if success=False)
            - expires_in: int - Seconds until token expires (if success=True)
        """
        self.log("Checking for valid access token...")

        try:
            # First, try to get a valid token from cache/file
            token_result = self.get_valid_token(token_file_path)
            if token_result and token_result.get('success'):
                # Double-check by actually testing the token with an API call
                self.log("Testing token validity with API call...")
                if self.test_token_validity():
                    self.log("Token validation successful")
                    return {
                        'success': True,
                        'access_token': token_result['tokens']['access_token'],
                        'expires_in': token_result.get('expires_in', 0),
                        'source': token_result.get('source', 'unknown')
                    }
                else:
                    self.log("Token failed API validation test")
                    return {
                        'success': False,
                        'error': 'Token exists but failed API validation test',
                        'details': 'Token was found and appears valid but failed when tested against the API'
                    }
            else:
                self.log("Unable to obtain valid token")
                return {
                    'success': False,
                    'error': 'Token not valid and could not be refreshed',
                    'details': 'Either no token file exists, token is expired and refresh failed, or no refresh token available'
                }

        except Exception as e:
            self.log(f"Exception while getting valid token: {str(e)}", "ERROR")
            return {
                'success': False,
                'error': f'Exception occurred: {str(e)}',
                'details': 'An unexpected error occurred while trying to get a valid token'
            }