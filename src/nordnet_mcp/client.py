import base64

import httpx


class SessionExpiredError(Exception):
    pass


class NordnetClient:
    def __init__(
        self,
        session_token: str | None,
        host: str = "public.nordnet.se",
        client_id: str | None = None,
    ):
        self.base_url = f"https://{host}/api/2"
        self.session_token = session_token
        self.client_id = client_id
        self._client = httpx.AsyncClient()

    def _auth_header(self) -> dict:
        token = self.session_token or ""
        creds = base64.b64encode(f"{token}:{token}".encode()).decode()
        headers = {"Authorization": f"Basic {creds}"}
        # Sessions appear tied to the client that created them: QR logins
        # (created with client-id NEXT) set this on the client, manual
        # tokens opt in via NORDNET_CLIENT_ID.
        if self.client_id:
            headers["client-id"] = self.client_id
        return headers

    async def get(self, path: str, params: dict | None = None) -> dict | list:
        resp = await self._client.get(
            f"{self.base_url}{path}",
            headers=self._auth_header(),
            params=params,
        )
        if resp.status_code == 401:
            client_id_hint = ""
            # Only worth suggesting when a client ID isn't configured yet.
            if not self.client_id:
                try:
                    error = resp.json()
                except ValueError:
                    error = None
                if isinstance(error, dict) and error.get("code") == "NEXT_INVALID_SESSION":
                    client_id_hint = (
                        "\nNordnet returned NEXT_INVALID_SESSION. Session IDs appear to be "
                        "tied to the client that created them. Add NORDNET_CLIENT_ID=NEXT "
                        "to your environment and restart the server."
                    )
            raise SessionExpiredError(
                "Session expired or not yet authenticated. Call the "
                "`nordnet_auth` tool to log in via QR code — scan it "
                "with the Nordnet mobile app and the session will be "
                "established automatically, no manual token needed. "
                "Alternatively, set a token by hand: log into nordnet.se, "
                "open DevTools → Application/Storage → Cookies, find "
                "NNX_SESSION_ID and copy its value into NORDNET_SESSION_TOKEN."
                + client_id_hint
            )
        resp.raise_for_status()
        if not resp.content:
            return []
        return resp.json()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()

    async def close(self):
        await self._client.aclose()
