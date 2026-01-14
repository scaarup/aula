import logging
import asyncio
from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class AulaAuthView(HomeAssistantView):
    url = "/api/aula/auth/{flow_id}"
    name = "api:aula:auth"
    requires_auth = False

    def __init__(self, hass):
        self.hass = hass

    async def get(self, request, flow_id):
        if (
            DOMAIN not in self.hass.data
            or "auth_sessions" not in self.hass.data[DOMAIN]
            or flow_id not in self.hass.data[DOMAIN]["auth_sessions"]
        ):
            return web.Response(status=404, text="Session not found or expired.")

        return web.Response(body=self._get_html(flow_id), content_type="text/html")

    def _get_html(self, flow_id):
        return f"""
<!DOCTYPE html>
<html>
<head>
    <title>Aula Authentication</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; text-align: center; padding: 20px; background-color: #f5f5f5; }}
        .container {{ max-width: 600px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        #qr-container {{ margin: 20px auto; min-height: 200px; display: flex; align-items: center; justify-content: center; }}
        #qr-container svg {{ max-width: 100%; height: auto; }}
        .hidden {{ display: none !important; }}
        #status {{ font-weight: 500; margin: 20px 0; font-size: 1.2em; color: #333; }}
        button {{ background-color: #03a9f4; color: white; border: none; padding: 10px 20px; margin: 5px; border-radius: 4px; cursor: pointer; font-size: 16px; }}
        button:hover {{ background-color: #0288d1; }}
        .error {{ color: #d32f2f; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Aula MitID Login</h1>
        <div id="status">Open your MitID app now...</div>
        <div id="qr-container"></div>
        <div id="identity-selection" class="hidden">
            <h3>Select Identity</h3>
            <div id="identity-buttons"></div>
        </div>
    </div>

    <script>
        const flowId = "{flow_id}";

        function updateStatus() {{
            fetch(`/api/aula/auth/${{flowId}}/status`)
                .then(response => response.json())
                .then(data => {{
                    if (data.error) {{
                        document.getElementById("status").innerText = data.error;
                        document.getElementById("status").className = "error";
                        return;
                    }}

                    if (data.message) {{
                        document.getElementById("status").innerText = data.message;
                    }}

                    if (data.qr_svg) {{
                        document.getElementById("qr-container").innerHTML = data.qr_svg;
                        document.getElementById("qr-container").classList.remove("hidden");
                        document.getElementById("identity-selection").classList.add("hidden");
                    }} else {{
                        document.getElementById("qr-container").innerHTML = "";
                    }}

                    if (data.identities && data.identities.length > 0) {{
                        const container = document.getElementById("identity-buttons");
                        container.innerHTML = "";
                        data.identities.forEach((name, index) => {{
                            const btn = document.createElement("button");
                            btn.innerText = name;
                            btn.onclick = () => selectIdentity(index + 1);
                            container.appendChild(btn);
                        }});
                        document.getElementById("identity-selection").classList.remove("hidden");
                        document.getElementById("qr-container").classList.add("hidden");
                    }}

                    if (data.completed) {{
                        document.getElementById("status").innerText = "Authentication successful! You can close this window.";
                        document.getElementById("qr-container").classList.add("hidden");
                        document.getElementById("identity-selection").classList.add("hidden");
                        if (!document.getElementById("close-btn")) {{
                            const btn = document.createElement("button");
                            btn.id = "close-btn";
                            btn.innerText = "Close Window";
                            btn.onclick = () => window.close();
                            document.querySelector(".container").appendChild(btn);
                        }}
                        return;
                    }}

                    setTimeout(updateStatus, 1000);
                }})
                .catch(err => {{
                    console.error(err);
                    setTimeout(updateStatus, 2000);
                }});
        }}

        function selectIdentity(index) {{
            fetch(`/api/aula/auth/${{flowId}}/select_identity`, {{
                method: "POST",
                headers: {{ "Content-Type": "application/json" }},
                body: JSON.stringify({{ identity: index }})
            }});
            document.getElementById("identity-selection").classList.add("hidden");
            document.getElementById("status").innerText = "Identity selected, continuing...";
        }}

        updateStatus();
    </script>
</body>
</html>
        """


class AulaAuthStatusView(HomeAssistantView):
    url = "/api/aula/auth/{flow_id}/status"
    name = "api:aula:auth:status"
    requires_auth = False

    def __init__(self, hass):
        self.hass = hass

    async def get(self, request, flow_id):
        if (
            DOMAIN not in self.hass.data
            or "auth_sessions" not in self.hass.data[DOMAIN]
            or flow_id not in self.hass.data[DOMAIN]["auth_sessions"]
        ):
            return web.json_response(
                {"error": "Session expired or not found"}, status=404
            )

        session = self.hass.data[DOMAIN]["auth_sessions"][flow_id]
        client = session["client"]

        qr_svg = None
        if not session.get("available_identities"):
            if hasattr(client, "get_qr_codes_svg"):
                import time

                svgs = client.get_qr_codes_svg()
                if svgs:
                    # Rotate QR code every second
                    idx = int(time.time()) % 2
                    qr_svg = svgs[idx]

        response = {
            "message": session.get("status_message", "Processing..."),
            "qr_svg": qr_svg,
            "identities": session.get("available_identities", []),
            "completed": session.get("completed", False),
            "error": session.get("error"),
        }
        return web.json_response(response)


class AulaAuthSelectIdentityView(HomeAssistantView):
    url = "/api/aula/auth/{flow_id}/select_identity"
    name = "api:aula:auth:select_identity"
    requires_auth = False

    def __init__(self, hass):
        self.hass = hass

    async def post(self, request, flow_id):
        if (
            DOMAIN not in self.hass.data
            or "auth_sessions" not in self.hass.data[DOMAIN]
            or flow_id not in self.hass.data[DOMAIN]["auth_sessions"]
        ):
            return web.json_response({"error": "Session not found"}, status=404)

        data = await request.json()
        identity_index = data.get("identity")

        session = self.hass.data[DOMAIN]["auth_sessions"][flow_id]
        future = session.get("identity_future")

        if future and not future.done():
            future.set_result(str(identity_index))
            session["available_identities"] = None

        return web.json_response({"status": "ok"})
