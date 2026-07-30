import os
from dataclasses import dataclass


@dataclass
class NordnetConfig:
    session_token: str | None
    host: str
    client_id: str | None = None


def load_config() -> NordnetConfig:
    """Load config from environment variables.

    A missing token is not an error here: the server can start without one
    and obtain it later via the `nordnet_auth` MCP App (QR login).
    Tools that hit the Nordnet API will raise SessionExpiredError until a
    token is set.
    """
    token = os.environ.get("NORDNET_SESSION_TOKEN") or None
    host = os.environ.get("NORDNET_HOST", "public.nordnet.se")
    client_id = os.environ.get("NORDNET_CLIENT_ID", "").strip() or None

    return NordnetConfig(session_token=token, host=host, client_id=client_id)
