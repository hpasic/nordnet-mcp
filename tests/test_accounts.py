# tests/test_accounts.py
import json
from unittest.mock import AsyncMock

import pytest

from nordnet_mcp.accounts import configure, register_tools

# Mock Nordnet API responses based on documented field names
MOCK_ACCOUNTS = [
    {"accno": 1, "type": "ISK", "default": True, "alias": "Min ISK"},
    {"accno": 2, "type": "AF", "default": False, "alias": "Aktie & fond"},
]

MOCK_POSITIONS = [
    {
        "instrument": {"instrument_id": 16120194, "name": "Novo Nordisk B"},
        "qty": 50,
        "acq_price_acc": {"value": 750.0, "currency": "DKK"},
        "market_value_acc": {"value": 890.0, "currency": "DKK"},
        "morning_pnl_acc": {"value": 140.0},
        "morning_pnl_pct": 18.67,
    }
]

MOCK_ACCOUNT_INFO = [{
    "account_sum_acc": {"value": 842350.0, "currency": "NOK"},
    "collateral_acc": {"value": 842350.0},
    "buying_power_acc": {"value": 45200.0},
    "interest": {"value": 0.0},
}]

MOCK_CURRENT_ACCOUNT_INFO = [{
    "account_sum": {"value": 159292.71, "currency": "NOK"},
    "collateral": {"value": 0.0, "currency": "NOK"},
    "trading_power": {"value": 159292.71, "currency": "NOK"},
    "interest": {"value": 0.0},
}]


@pytest.fixture
def mock_client():
    client = AsyncMock()
    client.get = AsyncMock()
    return client


@pytest.fixture
def app(mock_client):
    """Create a FastMCP app with account tools registered."""
    from mcp.server.fastmcp import FastMCP
    app = FastMCP("test")
    configure(mock_client)
    return register_tools(app)


@pytest.mark.asyncio
async def test_list_accounts(mock_client, app):
    mock_client.get.return_value = MOCK_ACCOUNTS

    tools = {t.name: t for t in await app.list_tools()}
    assert "list_accounts" in tools

    result = await app.call_tool("list_accounts", {})
    content, _meta = result
    data = json.loads(content[0].text)
    assert len(data) == 2
    assert data[0]["accno"] == 1


@pytest.mark.asyncio
async def test_get_positions(mock_client, app):
    mock_client.get.return_value = MOCK_POSITIONS

    result = await app.call_tool("get_positions", {"account_id": 1})
    content, _meta = result
    data = json.loads(content[0].text)
    assert len(data) == 1
    assert data[0]["name"] == "Novo Nordisk B"
    assert data[0]["quantity"] == 50
    assert data[0]["pnl"] == 140.0


@pytest.mark.asyncio
async def test_get_account_info(mock_client, app):
    mock_client.get.return_value = MOCK_ACCOUNT_INFO

    result = await app.call_tool("get_account_info", {"account_id": 1})
    content, _meta = result
    data = json.loads(content[0].text)
    assert data["buying_power"] == 45200.0

@pytest.mark.asyncio
async def test_get_account_info_current_plain_keys(mock_client, app):
    mock_client.get.return_value = MOCK_CURRENT_ACCOUNT_INFO

    result = await app.call_tool("get_account_info", {"account_id": 6})
    content, _meta = result
    data = json.loads(content[0].text)
    assert data["total_value"] == 159292.71
    assert data["buying_power"] == 159292.71
    assert data["currency"] == "NOK"
