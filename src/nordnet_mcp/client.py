import base64

import httpx


class SessionExpiredError(Exception):
    pass


class NordnetClient:
    def __init__(self, session_token: str | None, host: str = "public.nordnet.se"):
        self.base_url = f"https://{host}/api/2"
        self.session_token = session_token
        self._client = httpx.AsyncClient()

    def _auth_header(self) -> dict:
        token = self.session_token or ""
        creds = base64.b64encode(f"{token}:{token}".encode()).decode()
        # client-id is not optional - confirmed live, requests without it
        # get a 401 regardless of whether the token itself is valid.
        return {"Authorization": f"Basic {creds}", "client-id": "NEXT"}

    async def get(self, path: str, params: dict | None = None) -> dict | list:
        resp = await self._client.get(
            f"{self.base_url}{path}",
            headers=self._auth_header(),
            params=params,
        )
        if resp.status_code == 401:
            raise SessionExpiredError(
                "Session expired or not yet authenticated. Call the "
                "`nordnet_auth` tool to log in via QR code — scan it "
                "with the Nordnet mobile app and the session will be "
                "established automatically, no manual token needed."
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
