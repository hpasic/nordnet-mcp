import os
from unittest.mock import patch

import pytest

from nordnet_mcp import create_app


@pytest.fixture
def mock_env():
    """Set env vars for a test session token."""
    with patch.dict(os.environ, {
        "NORDNET_SESSION_TOKEN": "test_token_123",
        "NORDNET_HOST": "public.nordnet.se",
    }):
        yield


@pytest.mark.asyncio
async def test_create_app_registers_all_tools(mock_env):
    """Verify all 14 tools are registered."""
    app = create_app()

    tools = await app.list_tools()
    tool_names = {t.name for t in tools}

    # Accounts (7)
    assert "list_accounts" in tool_names
    assert "get_account_info" in tool_names
    assert "get_positions" in tool_names
    assert "get_trades" in tool_names
    assert "get_ledgers" in tool_names
    assert "get_orders" in tool_names
    assert "get_daily_transactions" in tool_names

    # Instruments (4)
    assert "get_instrument" in tool_names
    assert "lookup_instrument" in tool_names
    assert "search_stocks" in tool_names
    assert "check_suitability" in tool_names

    # Reference (3)
    assert "get_countries" in tool_names
    assert "get_instrument_types" in tool_names
    assert "get_search_attributes" in tool_names

    # Login (2)
    assert "nordnet_auth" in tool_names
    assert "nordnet_login_poll" in tool_names

    assert len(tool_names) == 16


def test_create_app_no_token_does_not_raise():
    # The server must be able to start without a token, since the QR login
    # flow is how it obtains one in the first place.
    with patch.dict(os.environ, {}, clear=True):
        app = create_app()

    assert app is not None
