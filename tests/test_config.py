import os
from unittest.mock import patch

from nordnet_mcp.config import load_config


def test_load_config_from_env():
    with patch.dict(os.environ, {
        "NORDNET_SESSION_TOKEN": "my_token",
        "NORDNET_HOST": "public.nordnet.no",
    }):
        config = load_config()

    assert config.session_token == "my_token"
    assert config.host == "public.nordnet.no"


def test_load_config_no_token_is_none():
    # No token is not an error: the server can start and obtain one later
    # via the nordnet_auth QR flow.
    with patch.dict(os.environ, {}, clear=True):
        config = load_config()

    assert config.session_token is None


def test_load_config_default_host():
    with patch.dict(os.environ, {"NORDNET_SESSION_TOKEN": "tok"}, clear=True):
        config = load_config()

    assert config.host == "public.nordnet.se"
