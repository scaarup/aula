import requests, time, hashlib, base64, hmac, qrcode, threading, json, logging
from .CustomSRP import CustomSRP, hex_to_bytes, bytes_to_hex, pad

_LOGGER = logging.getLogger(__name__)


class BrowserClient:
    def __init__(
        self,
        client_hash: str,
        authentication_session_id: str,
        requests_session=requests.Session(),
    ):
        self.qr_display_thread_lock = threading.Lock()
        self.session = requests_session

        self.client_hash = client_hash
        self.authentication_session_id = authentication_session_id

        url = f"https://www.mitid.dk/mitid-core-client-backend/v1/authentication-sessions/{authentication_session_id}"
        _LOGGER.debug(f"Starting authentication session request to %s", url)
        r = self.session.get(url)
        if r.status_code != 200:
            _LOGGER.error(
                f"Failed to get authentication session ({authentication_session_id}), status code {r.status_code}"
            )
            raise Exception(r.content)

        r = r.json()
        # This is all needed for flowValueProofs later on
        self.broker_security_context = r["brokerSecurityContext"]
        self.service_provider_name = r["serviceProviderName"]
        self.reference_text_header = r["referenceTextHeader"]
        self.reference_text_body = r["referenceTextBody"]
        self.status_message = (
            f"Beginning login session for {self.service_provider_name}"
        )
        _LOGGER.info(f"Beginning login session for {self.service_provider_name}")
        _LOGGER.debug(f"{self.reference_text_header}")
        _LOGGER.debug(f"{self.reference_text_body}")

    def __display_qr_ascii(self, stop_event):

        frame = True
        while not stop_event.is_set():
            # os.system("cls" if os.name == "nt" else "clear")
            # print("Scan this QR Code in the app:")
            qr1, qr2 = self.__get_qr_codes()
            # print(render_qr(qr1) if frame else render_qr(qr2))
            frame = not frame
            stop_event.wait(1)

    def __set_qr_codes(self, qr1, qr2):
        with self.qr_display_thread_lock:
            self.qr1 = qr1
            self.qr2 = qr2

    def __get_qr_codes(self):
        with self.qr_display_thread_lock:
            return self.qr1, self.qr2

    def get_current_qr_codes(self):
        """Get current QR codes for external display (e.g., Home Assistant GUI)."""
        try:
            return self.__get_qr_codes()
        except AttributeError:
            return None

    def start_app_authentication(self):
        """Start APP authentication and return poll information."""
        self.__select_authenticator("APP")

        r = self.session.post(
            f"https://www.mitid.dk/mitid-code-app-auth/v1/authenticator-sessions/web/{self.current_authenticator_session_id}/init-auth",
            json={},
        )
        if r.status_code != 200:
            raise Exception(f"Failed to request app login, status code {r.status_code}")

        r = r.json()
        if (
            "errorCode" in r
            and r["errorCode"]
            == "auth.codeapp.authentication.parallel_sessions_detected"
        ):
            raise Exception("Parallel app sessions detected")

        self.poll_url = r["pollUrl"]
        self.ticket = r["ticket"]
        self.auth_status = "waiting"
        self.status_message = "Waiting for MitID app"
        self.otp_code = None
        return {"poll_url": self.poll_url, "ticket": self.ticket}

    def poll_app_authentication_status(self):
        """Poll once for APP authentication status. Returns status dict."""
        if not hasattr(self, "poll_url"):
            raise Exception(
                "Authentication not started - call start_app_authentication() first"
            )

        r = self.session.post(self.poll_url, json={"ticket": self.ticket})

        if r.status_code != 200:
            return {"status": "error", "message": "Poll request failed"}

        data = r.json()
        status = data.get("status")

        if status == "timeout":
            return {"status": "waiting", "message": "Waiting for response..."}

        elif status == "channel_validation_otp":
            self.otp_code = data["channelBindingValue"]
            self.status_message = f"Use OTP code: {self.otp_code}"
            return {
                "status": "otp_ready",
                "message": self.status_message,
                "otp_code": self.otp_code,
            }

        elif status == "channel_validation_tqr":
            # Generate QR codes
            channel_binding = data["channelBindingValue"]
            half = int(len(channel_binding) / 2)

            qr_data_1 = {
                "v": 1,
                "p": 1,
                "t": 2,
                "h": channel_binding[:half],
                "uc": data["updateCount"],
            }
            qr1 = qrcode.QRCode(border=1)
            qr1.add_data(json.dumps(qr_data_1, separators=(",", ":")))
            qr1.make()

            qr_data_2 = {
                "v": 1,
                "p": 2,
                "t": 2,
                "h": channel_binding[half:],
                "uc": data["updateCount"],
            }
            qr2 = qrcode.QRCode(border=1)
            qr2.add_data(json.dumps(qr_data_2, separators=(",", ":")))
            qr2.make()

            self.__set_qr_codes(qr1, qr2)
            self.status_message = "Scan QR code with MitID app"
            return {"status": "qr_ready", "message": self.status_message}

        elif status == "channel_verified":
            self.status_message = "QR verified, waiting for approval"
            return {"status": "verified", "message": self.status_message}

        elif status == "OK" and data.get("confirmation") == True:
            # Authentication completed
            self.auth_response = data["payload"]["response"]
            self.auth_response_signature = data["payload"]["responseSignature"]
            return {"status": "completed", "message": "Authentication successful"}

        else:
            return {"status": "error", "message": "Authentication was not accepted"}

    def complete_app_authentication(self):
        """Complete APP authentication after polling succeeds. Returns finalization session ID."""
        if not hasattr(self, "auth_response"):
            raise Exception(
                "Authentication not completed - poll until status is 'completed'"
            )

        # SRP protocol stages
        timer_1 = time.time()
        SRP = CustomSRP()
        A = SRP.SRPStage1()

        r = self.session.post(
            f"https://www.mitid.dk/mitid-code-app-auth/v1/authenticator-sessions/web/{self.current_authenticator_session_id}/init",
            json={"randomA": {"value": A}},
        )
        if r.status_code != 200:
            raise Exception(f"Failed to init app protocol, status code {r.status_code}")

        srpSalt = r.json()["srpSalt"]["value"]
        randomB = r.json()["randomB"]["value"]

        m = hashlib.sha256()
        m.update(
            base64.b64decode(self.auth_response)
            + self.current_authenticator_session_flow_key.encode("utf8")
        )
        password = m.hexdigest()

        m1 = SRP.SRPStage3(
            srpSalt, randomB, password, self.current_authenticator_session_id
        )

        unhashed_flow_value_proof = self.__create_flow_value_proof()
        m = hashlib.sha256()
        unhashed_flow_value_proof_key = "flowValues" + bytes_to_hex(SRP.K_bits)
        m.update(unhashed_flow_value_proof_key.encode("utf8"))
        hashed_flow_value_proof_key = m.hexdigest()
        hmac_flow_value_proof = hmac.new(
            hex_to_bytes(hashed_flow_value_proof_key),
            unhashed_flow_value_proof,
            hashlib.sha256,
        ).digest()
        flow_value_proof = base64.b64encode(hmac_flow_value_proof).decode("ascii")

        # Complete authentication
        r = self.session.post(
            f"https://www.mitid.dk/mitid-code-app-auth/v1/authenticator-sessions/web/{self.current_authenticator_session_id}/complete",
            json={"M1": {"value": m1}, "flowValueProof": {"value": flow_value_proof}},
        )
        if r.status_code != 200:
            raise Exception(
                f"Failed to complete app authentication, status code {r.status_code}"
            )

        self.status_message = "App login was accepted, finalizing authentication"
        _LOGGER.info(
            "App login was accepted, you can now finalize authentication and receive your authorization code"
        )
        self.finalization_authentication_session_id = r.json()[
            "authenticationSessionId"
        ]
        return self.finalization_authentication_session_id

    def __convert_human_authenticator_name_to_combination_id(self, authenticator_name):
        match authenticator_name:
            case "APP":
                return "S3"
            case "TOKEN":
                return "S1"
            case _:
                raise Exception(f"No such authenticator name ({authenticator_name})")

    def __convert_combination_id_to_human_authenticator_name(self, combination_id):
        match combination_id:
            case "S3":
                return "APP"
            case "L2":
                return "APP"
            case "S1":
                return "TOKEN"
            case _:
                raise Exception(f"No such combination ID ({combination_id})")

    def identify_as_user_and_get_available_authenticators(self, user_id):
        url = f"https://www.mitid.dk/mitid-core-client-backend/v1/authentication-sessions/{self.authentication_session_id}"
        _LOGGER.debug("Requesting available methods from %s", url)
        self.user_id = user_id
        r = self.session.put(
            url,
            json={"identityClaim": user_id},
        )

        if r.status_code != 200:
            _LOGGER.error(
                f"Received status code ({r.status_code}) while attempting to identify as user ({user_id})"
            )
            if (
                r.status_code == 400
                and r.json()["errorCode"] == "control.identity_not_found"
            ):
                _LOGGER.error(f"User '{user_id}' does not exist.")
                raise Exception(r.content)

            if (
                r.status_code == 400
                and r.json()["errorCode"] == "control.authentication_session_not_found"
            ):
                _LOGGER.error("Authentication session not found")
                raise Exception(r.content)

            raise Exception(r.content)

        r = self.session.post(
            f"https://www.mitid.dk/mitid-core-client-backend/v2/authentication-sessions/{self.authentication_session_id}/next",
            json={"combinationId": ""},
        )

        if r.status_code != 200:
            _LOGGER.error(
                f"Received status code ({r.status_code}) while attempting to get authenticators for user ({user_id})"
            )
            raise Exception(r.content)

        r = r.json()
        if (
            r["errors"]
            and len(r["errors"]) > 0
            and r["errors"][0]["errorCode"] == "control.authenticator_cannot_be_started"
        ):
            error_text = r["errors"][0]["userMessage"]["text"]["text"]
            _LOGGER.error(
                f"Could not get authenticators, got the following error text: {error_text}"
            )
            raise Exception(error_text)

        self.current_authenticator_type = r["nextAuthenticator"]["authenticatorType"]
        self.current_authenticator_session_flow_key = r["nextAuthenticator"][
            "authenticatorSessionFlowKey"
        ]
        self.current_authenticator_eafe_hash = r["nextAuthenticator"]["eafeHash"]
        self.current_authenticator_session_id = r["nextAuthenticator"][
            "authenticatorSessionId"
        ]

        available_combinations = r["combinations"]
        available_authenticators = {}
        for available_combination in available_combinations:
            available_authenticators[
                self.__convert_combination_id_to_human_authenticator_name(
                    available_combination["id"]
                )
            ] = available_combination["combinationItems"][0]["name"]

        return available_authenticators

    def __create_flow_value_proof(self):
        hashed_broker_security_context = hashlib.sha256(
            self.broker_security_context.encode("utf8")
        ).hexdigest()
        base64_reference_text_header = base64.b64encode(
            (self.reference_text_header.encode("utf8"))
        ).decode("ascii")
        base64_reference_text_body = base64.b64encode(
            (self.reference_text_body.encode("utf8"))
        ).decode("ascii")
        base64_service_provider_name = base64.b64encode(
            (self.service_provider_name.encode("utf8"))
        ).decode("ascii")
        return f"{self.current_authenticator_session_id},{self.current_authenticator_session_flow_key},{self.client_hash},{self.current_authenticator_eafe_hash},{hashed_broker_security_context},{base64_reference_text_header},{base64_reference_text_body},{base64_service_provider_name}".encode(
            "utf-8"
        )

    def __select_authenticator(self, authenticator_type: str):
        if authenticator_type == self.current_authenticator_type:
            return

        combination_id = self.__convert_human_authenticator_name_to_combination_id(
            authenticator_type
        )

        r = self.session.post(
            f"https://www.mitid.dk/mitid-core-client-backend/v2/authentication-sessions/{self.authentication_session_id}/next",
            json={"combinationId": combination_id},
        )

        if r.status_code != 200:
            _LOGGER.error(
                f"Received status code ({r.status_code}) while attempting to get authenticators for user ({self.user_id})"
            )
            raise Exception(r.content)

        r = r.json()
        if (
            r["errors"]
            and len(r["errors"]) > 0
            and r["errors"][0]["errorCode"] == "control.authenticator_cannot_be_started"
        ):
            error_text = r["errors"][0]["userMessage"]["text"]["text"]
            _LOGGER.error(
                f"Could not get authenticators, got the following error text: {error_text}"
            )
            raise Exception(error_text)

        self.current_authenticator_type = r["nextAuthenticator"]["authenticatorType"]
        self.current_authenticator_session_flow_key = r["nextAuthenticator"][
            "authenticatorSessionFlowKey"
        ]
        self.current_authenticator_eafe_hash = r["nextAuthenticator"]["eafeHash"]
        self.current_authenticator_session_id = r["nextAuthenticator"][
            "authenticatorSessionId"
        ]

        if self.current_authenticator_type != authenticator_type:
            raise Exception(
                f"Was not able to choose the desired authenticator ({authenticator_type}), instead we received ({self.current_authenticator_type})"
            )

    def authenticate_with_token(self, token_digits: str):
        self.__select_authenticator("TOKEN")

        timer_1 = time.time()
        SRP = CustomSRP()
        A = SRP.SRPStage1()
        timer_1 = time.time() - timer_1

        r = self.session.post(
            f"https://www.mitid.dk/mitid-code-token-auth/v1/authenticator-sessions/{self.current_authenticator_session_id}/codetoken-init",
            json={"randomA": {"value": A}},
        )
        if r.status_code != 200:
            _LOGGER.error(
                f"Failed to init TOTP code protocol, status code {r.status_code}"
            )
            raise Exception(r.content)

        timer_2 = time.time()
        r = r.json()
        # pbkdfSalt is not actually used even though we receive it, what the hell are they doing here?
        # This seems like schlock
        # pbkdfSalt = r["pbkdf2Salt"]["value"]
        srpSalt = r["srpSalt"]["value"]
        randomB = r["randomB"]["value"]

        m1 = SRP.SRPStage3(
            srpSalt,
            randomB,
            bytes_to_hex(self.current_authenticator_session_flow_key.encode("utf-8")),
            self.current_authenticator_session_id,
        )

        unhashed_flow_value_proof = self.__create_flow_value_proof()
        m = hashlib.sha256()
        unhashed_flow_value_proof_key = "OTP" + token_digits + bytes_to_hex(SRP.K_bits)
        m.update(unhashed_flow_value_proof_key.encode("utf8"))
        flow_value_proof_key = m.digest()

        flow_value_proof = hmac.new(
            flow_value_proof_key, unhashed_flow_value_proof, hashlib.sha256
        ).hexdigest()

        timer_2 = time.time() - timer_2
        front_end_processing_time = int((timer_1 + timer_2) * 1000)

        r = self.session.post(
            f"https://www.mitid.dk/mitid-code-token-auth/v1/authenticator-sessions/{self.current_authenticator_session_id}/codetoken-prove",
            json={
                "m1": {"value": m1},
                "flowValueProof": {"value": flow_value_proof},
                "frontEndProcessingTime": front_end_processing_time,
            },
        )
        if r.status_code != 204:
            _LOGGER.error(f"Failed to submit TOTP code, status code {r.status_code}")
            raise Exception(r.content)

        r = self.session.post(
            f"https://www.mitid.dk/mitid-core-client-backend/v2/authentication-sessions/{self.authentication_session_id}/next",
            json={"combinationId": ""},
        )
        if r.status_code != 200:
            _LOGGER.error(f"Failed to prove TOTP code, status code {r.status_code}")
            raise Exception(r.content)

        if (
            r.json()["errors"]
            and len(r.json()["errors"]) > 0
            and r.json()["errors"][0]["errorCode"] == "TOTP_INVALID"
        ):
            error_text = r.json()["errors"][0]["message"]
            _LOGGER.error(
                f"Could not log in with the provided TOTP code, got the following message: {error_text}"
            )
            raise Exception(r.content)

        r = r.json()
        if (
            "nextAuthenticator" not in r
            or "authenticatorType" not in r["nextAuthenticator"]
            or r["nextAuthenticator"]["authenticatorType"] != "PASSWORD"
        ):
            _LOGGER.error(
                f"Ran into an unexpected situation, was expecting to be asked for password after TOTP but got the following response"
            )
            raise Exception(r.content)

        self.current_authenticator_type = r["nextAuthenticator"]["authenticatorType"]
        self.current_authenticator_session_flow_key = r["nextAuthenticator"][
            "authenticatorSessionFlowKey"
        ]
        self.current_authenticator_eafe_hash = r["nextAuthenticator"]["eafeHash"]
        self.current_authenticator_session_id = r["nextAuthenticator"][
            "authenticatorSessionId"
        ]
        _LOGGER.info("Token code accepted, you now need to validate your password")

    def authenticate_with_password(self, password: str):
        if self.current_authenticator_type != "PASSWORD":
            raise Exception(
                f"You cannot authenticate with password before completing authentication with token code, the current authenticator type was ({self.current_authenticator_type})"
            )

        timer_1 = time.time()
        SRP = CustomSRP()
        A = SRP.SRPStage1()
        timer_1 = time.time() - timer_1

        r = self.session.post(
            f"https://www.mitid.dk/mitid-password-auth/v1/authenticator-sessions/{self.current_authenticator_session_id}/init",
            json={"randomA": {"value": A}},
        )
        if r.status_code != 200:
            _LOGGER.error(
                f"Failed to init password protocol, status code {r.status_code}"
            )
            raise Exception(r.content)

        timer_2 = time.time()
        r = r.json()
        pbkdfSalt = r["pbkdf2Salt"]["value"]
        srpSalt = r["srpSalt"]["value"]
        randomB = r["randomB"]["value"]

        password = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), hex_to_bytes(pbkdfSalt), 20000, 32
        ).hex()

        m1 = SRP.SRPStage3(
            srpSalt, randomB, password, self.current_authenticator_session_id
        )

        unhashed_flow_value_proof = self.__create_flow_value_proof()
        m = hashlib.sha256()
        unhashed_flow_value_proof_key = "flowValues" + bytes_to_hex(SRP.K_bits)
        m.update(unhashed_flow_value_proof_key.encode("utf8"))
        flow_value_proof_key = m.digest()

        flow_value_proof = hmac.new(
            flow_value_proof_key, unhashed_flow_value_proof, hashlib.sha256
        ).hexdigest()

        timer_2 = time.time() - timer_2
        front_end_processing_time = int((timer_1 + timer_2) * 1000)

        r = self.session.post(
            f"https://www.mitid.dk/mitid-password-auth/v1/authenticator-sessions/{self.current_authenticator_session_id}/password-prove",
            json={
                "m1": {"value": m1},
                "flowValueProof": {"value": flow_value_proof},
                "frontEndProcessingTime": front_end_processing_time,
            },
        )
        if r.status_code != 204:
            _LOGGER.error(f"Failed to submit password, status code {r.status_code}")
            raise Exception(r.content)

        r = self.session.post(
            f"https://www.mitid.dk/mitid-core-client-backend/v2/authentication-sessions/{self.authentication_session_id}/next",
            json={"combinationId": ""},
        )
        if r.status_code != 200:
            _LOGGER.error(f"Failed to prove password, status code {r.status_code}")
            raise Exception(r.content)

        r = r.json()
        if r["errors"] and len(r["errors"]) > 0:
            if r["errors"][0]["errorCode"] == "PASSWORD_INVALID":
                error_text = r["errors"][0]["message"]
                _LOGGER.error(
                    f"Could not log in with the provided password, got the following message: {error_text}"
                )
                raise Exception(error_text)
            elif r["errors"][0]["errorCode"] == "core.psd2.error":
                error_text = r["errors"][0]["message"]
                _LOGGER.error(
                    f"Could not log in due to an error, probably due to a wrong password provided. Got the following message: {error_text}"
                )
                raise Exception(error_text)
            else:
                error_text = r["errors"][0]["message"]
                _LOGGER.error(
                    f"Could not log in due to an unknown error, got the following message: {error_text}"
                )
                raise Exception(error_text)

        self.finalization_authentication_session_id = r["nextSessionId"]
        self.status_message = "Password accepted, finalizing authentication"
        _LOGGER.info(
            "Password was accepted, you can now finalize authentication and receive your authorization code"
        )

    def authenticate_with_app(self):
        self.__select_authenticator("APP")

        r = self.session.post(
            f"https://www.mitid.dk/mitid-code-app-auth/v1/authenticator-sessions/web/{self.current_authenticator_session_id}/init-auth",
            json={},
        )
        if r.status_code != 200:
            _LOGGER.error(f"Failed to request app login, status code {r.status_code}")
            raise Exception(r.content)

        r = r.json()
        if (
            "errorCode" in r
            and r["errorCode"]
            == "auth.codeapp.authentication.parallel_sessions_detected"
        ):
            _LOGGER.error(
                "Parallel app sessions detected, only a single app login session can be happening at any one time"
            )
            raise Exception(
                "Parallel app sessions detected. Please wait a few minutes before trying again."
            )

        poll_url = r["pollUrl"]
        ticket = r["ticket"]
        self.status_message = "Login request has been made, open your MitID app now"
        _LOGGER.info("Login request has been made, open your MitID app now")
        qr_stop_event = None
        qr_display_thread = None
        while True:
            r = self.session.post(poll_url, json={"ticket": ticket})

            if r.status_code == 200 and r.json()["status"] == "timeout":
                continue

            if r.status_code == 200 and r.json()["status"] == "channel_validation_otp":
                self.status_message = f"Please use the following OTP code in the app: {r.json()['channelBindingValue']}"
                _LOGGER.info(
                    f"Please use the following OTP code in the app: {r.json()['channelBindingValue']}"
                )
                continue

            if r.status_code == 200 and r.json()["status"] == "channel_validation_tqr":
                qr_data = {
                    "v": 1,
                    "p": 1,
                    "t": 2,
                    "h": r.json()["channelBindingValue"][
                        : int(len(r.json()["channelBindingValue"]) / 2)
                    ],
                    "uc": r.json()["updateCount"],
                }
                qr1 = qrcode.QRCode(border=1)
                qr1.add_data(json.dumps(qr_data, separators=(",", ":")))
                qr1.make()

                qr_data["p"] = 2
                qr_data["h"] = r.json()["channelBindingValue"][
                    int(len(r.json()["channelBindingValue"]) / 2) :
                ]

                qr2 = qrcode.QRCode(border=1)
                qr2.add_data(json.dumps(qr_data, separators=(",", ":")))
                qr2.make()

                self.__set_qr_codes(qr1, qr2)

                if qr_stop_event is None:
                    qr_stop_event = threading.Event()
                    qr_display_thread = threading.Thread(
                        target=self.__display_qr_ascii, args=[qr_stop_event]
                    )
                    qr_display_thread.start()

                continue

            if r.status_code == 200 and r.json()["status"] == "channel_verified":
                if qr_display_thread and qr_display_thread.is_alive():
                    qr_stop_event.set()
                    qr_display_thread.join()
                self.status_message = "The OTP/QR code has been verified, now waiting user to approve login"
                _LOGGER.info(
                    "The OTP/QR code has been verified, now waiting user to approve login"
                )
                continue

            if not (
                r.status_code == 200
                and r.json()["status"] == "OK"
                and r.json()["confirmation"] == True
            ):
                if qr_display_thread and qr_display_thread.is_alive():
                    qr_stop_event.set()
                    qr_display_thread.join()
                _LOGGER.error("Login request was not accepted")
                raise Exception(r.content)

            break

        r = r.json()
        response = r["payload"]["response"]
        response_signature = r["payload"]["responseSignature"]

        timer_1 = time.time()
        SRP = CustomSRP()
        A = SRP.SRPStage1()
        timer_1 = time.time() - timer_1

        r = self.session.post(
            f"https://www.mitid.dk/mitid-code-app-auth/v1/authenticator-sessions/web/{self.current_authenticator_session_id}/init",
            json={"randomA": {"value": A}},
        )
        if r.status_code != 200:
            _LOGGER.error(f"Failed to init app protocol, status code {r.status_code}")
            raise Exception(r.content)

        timer_2 = time.time()
        srpSalt = r.json()["srpSalt"]["value"]
        randomB = r.json()["randomB"]["value"]

        m = hashlib.sha256()
        m.update(
            base64.b64decode(response)
            + self.current_authenticator_session_flow_key.encode("utf8")
        )
        password = m.hexdigest()

        m1 = SRP.SRPStage3(
            srpSalt, randomB, password, self.current_authenticator_session_id
        )

        unhashed_flow_value_proof = self.__create_flow_value_proof()
        m = hashlib.sha256()
        unhashed_flow_value_proof_key = "flowValues" + bytes_to_hex(SRP.K_bits)
        m.update(unhashed_flow_value_proof_key.encode("utf8"))
        flow_value_proof_key = m.digest()

        flow_value_proof = hmac.new(
            flow_value_proof_key, unhashed_flow_value_proof, hashlib.sha256
        ).hexdigest()

        timer_2 = time.time() - timer_2

        r = self.session.post(
            f"https://www.mitid.dk/mitid-code-app-auth/v1/authenticator-sessions/web/{self.current_authenticator_session_id}/prove",
            json={"m1": {"value": m1}, "flowValueProof": {"value": flow_value_proof}},
        )
        if r.status_code != 200:
            _LOGGER.error(
                f"Failed to submit app response proof, status code {r.status_code}"
            )
            raise Exception(r.content)

        timer_3 = time.time()
        m2 = r.json()["m2"]["value"]
        if not SRP.SRPStage5(m2):
            raise Exception("m2 could not be validated during proving of app response")
        auth_enc = base64.b64encode(
            SRP.AuthEnc(base64.b64decode(pad(response_signature)))
        ).decode("ascii")
        timer_3 = time.time() - timer_3

        front_end_processing_time = int((timer_1 + timer_2 + timer_3) * 1000)

        r = self.session.post(
            f"https://www.mitid.dk/mitid-code-app-auth/v1/authenticator-sessions/web/{self.current_authenticator_session_id}/verify",
            json={
                "encAuth": auth_enc,
                "frontEndProcessingTime": front_end_processing_time,
            },
        )
        if r.status_code != 204:
            _LOGGER.error(
                f"Failed to verify app response signature, status code {r.status_code}"
            )
            raise Exception(r.content)

        r = self.session.post(
            f"https://www.mitid.dk/mitid-core-client-backend/v2/authentication-sessions/{self.authentication_session_id}/next",
            json={"combinationId": ""},
        )
        if r.status_code != 200:
            _LOGGER.error(f"Failed to prove app login, status code {r.status_code}")
            raise Exception(r.content)

        r = r.json()
        if r["errors"] and len(r["errors"]) > 0:
            _LOGGER.error(f"Could not prove the app login")
            raise Exception("Could not prove the app login. Please try again.")

        self.finalization_authentication_session_id = r["nextSessionId"]
        self.status_message = "App login was accepted, finalizing authentication"
        _LOGGER.info(
            "App login was accepted, you can now finalize authentication and receive your authorization code"
        )

    def finalize_authentication_and_get_authorization_code(self):
        if not self.finalization_authentication_session_id:
            raise Exception(
                "No finalization session ID set, make sure you have completed an authentication flow."
            )

        r = self.session.put(
            f"https://www.mitid.dk/mitid-core-client-backend/v1/authentication-sessions/{self.finalization_authentication_session_id}/finalization"
        )
        if r.status_code != 200:
            _LOGGER.error(
                f"Failed to retrieve authorization code, status code {r.status_code}"
            )
            raise Exception(r.content)

        return r.json()["authorizationCode"]
