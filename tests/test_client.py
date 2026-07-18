import base64

import httpx
import pytest
import pytest_asyncio
import respx

from nordnet_mcp.client import NordnetClient, SessionExpiredError


@pytest_asyncio.fixture
async def client():
    c = NordnetClient(session_token="test_token", host="public.nordnet.se")
    yield c
    await c.close()


def test_auth_header():
    client = NordnetClient(session_token="test_token", host="public.nordnet.se")
    expected = base64.b64encode(b"test_token:test_token").decode()
    headers = client._auth_header()
    assert headers["Authorization"] == f"Basic {expected}"
    # Confirmed live: requests without this get a 401 regardless of token validity.
    assert headers["client-id"] == "NEXT"


@respx.mock
@pytest.mark.asyncio
async def test_get_success(client):
    respx.get("https://public.nordnet.se/api/2/accounts").mock(
        return_value=httpx.Response(200, json=[{"account_id": 1}])
    )

    result = await client.get("/accounts")
    assert result == [{"account_id": 1}]


@respx.mock
@pytest.mark.asyncio
async def test_get_with_params(client):
    route = respx.get("https://public.nordnet.se/api/2/accounts/1/trades").mock(
        return_value=httpx.Response(200, json=[])
    )

    await client.get("/accounts/1/trades", params={"days": 7})
    assert route.calls[0].request.url.params["days"] == "7"


@respx.mock
@pytest.mark.asyncio
async def test_get_401_raises_session_expired(client):
    respx.get("https://public.nordnet.se/api/2/accounts").mock(
        return_value=httpx.Response(401)
    )

    with pytest.raises(SessionExpiredError) as exc_info:
        await client.get("/accounts")

    message = str(exc_info.value)
    assert "Session expired" in message
    assert "nordnet_auth" in message


@respx.mock
@pytest.mark.asyncio
async def test_get_empty_response(client):
    respx.get("https://public.nordnet.se/api/2/accounts/1/trades").mock(
        return_value=httpx.Response(200, content=b"")
    )

    result = await client.get("/accounts/1/trades")
    assert result == []


@respx.mock
@pytest.mark.asyncio
async def test_get_500_raises_http_error(client):
    respx.get("https://public.nordnet.se/api/2/accounts").mock(
        return_value=httpx.Response(500)
    )

    with pytest.raises(httpx.HTTPStatusError):
        await client.get("/accounts")
