import os
from unittest.mock import patch

import pytest

from nordnet_mcp.config import load_config


def test_load_config_from_env():
    with patch.dict(os.environ, {
        "NORDNET_SESSION_TOKEN": "my_token",
        "NORDNET_HOST": "public.nordnet.no",
        "NORDNET_CLIENT_ID": "NEXT",
    }):
        config = load_config()

    assert config.session_token == "my_token"
    assert config.host == "public.nordnet.no"
    assert config.client_id == "NEXT"


def test_load_config_no_token_raises():
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError, match="No session token"):
            load_config()


def test_load_config_default_host():
    with patch.dict(os.environ, {"NORDNET_SESSION_TOKEN": "tok"}, clear=True):
        config = load_config()

    assert config.host == "public.nordnet.se"
    assert config.client_id is None


def test_load_config_client_id_stripped():
    with patch.dict(os.environ, {
        "NORDNET_SESSION_TOKEN": "tok",
        "NORDNET_CLIENT_ID": " NEXT ",
    }, clear=True):
        config = load_config()

    assert config.client_id == "NEXT"


def test_load_config_blank_client_id_is_none():
    with patch.dict(os.environ, {
        "NORDNET_SESSION_TOKEN": "tok",
        "NORDNET_CLIENT_ID": "   ",
    }, clear=True):
        config = load_config()

    assert config.client_id is None
