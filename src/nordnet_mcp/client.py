import base64

import httpx


class SessionExpiredError(Exception):
    pass


class NordnetClient:
    def __init__(
        self,
        session_token: str,
        host: str = "public.nordnet.se",
        client_id: str | None = None,
    ):
        self.base_url = f"https://{host}/api/2"
        self.session_token = session_token
        self.client_id = client_id
        self._client = httpx.AsyncClient()

    def _auth_header(self) -> dict:
        creds = base64.b64encode(
            f"{self.session_token}:{self.session_token}".encode()
        ).decode()
        headers = {"Authorization": f"Basic {creds}"}
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
            try:
                error = resp.json()
            except ValueError:
                error = None

            client_id_hint = ""
            if isinstance(error, dict) and error.get("code") == "NEXT_INVALID_SESSION":
                client_id_hint = (
                    "\nNordnet returned NEXT_INVALID_SESSION. Session IDs appear to be "
                    "tied to the client that created them. Add NORDNET_CLIENT_ID=NEXT "
                    "to your environment and restart the server."
                )
            raise SessionExpiredError(
                "Session expired. Refresh your token:\n"
                "1. Log into nordnet.se\n"
                "2. Open DevTools → Application/Storage → Cookies\n"
                "3. Select the Nordnet domain and find NNX_SESSION_ID\n"
                "4. Copy that cookie value into NORDNET_SESSION_TOKEN"
                f"{client_id_hint}"
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
