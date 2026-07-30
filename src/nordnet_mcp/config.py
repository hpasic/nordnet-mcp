import os
from dataclasses import dataclass


@dataclass
class NordnetConfig:
    session_token: str
    host: str
    client_id: str | None = None


def load_config() -> NordnetConfig:
    """Load config from environment variables."""
    token = os.environ.get("NORDNET_SESSION_TOKEN")
    host = os.environ.get("NORDNET_HOST", "public.nordnet.se")
    client_id = os.environ.get("NORDNET_CLIENT_ID", "").strip() or None

    if not token:
        raise ValueError(
            "No session token found. Set NORDNET_SESSION_TOKEN in .env "
            "or as an environment variable."
        )

    return NordnetConfig(session_token=token, host=host, client_id=client_id)
