import asyncio
import json

import httpx
import pytest
import respx

from nordnet_mcp import auth
from nordnet_mcp.client import NordnetClient


@pytest.fixture(autouse=True)
def reset_login_state():
    """auth.py keeps module-level state; isolate each test."""
    original_verify_interval = auth.KEEPALIVE_VERIFY_INTERVAL_SECONDS
    original_token_interval = auth.KEEPALIVE_TOKEN_INTERVAL_SECONDS
    auth._pending = None
    auth._client = None
    auth._market = "se"
    auth._ntag = auth.NTAG_SENTINEL
    yield
    auth._pending = None
    auth._client = None
    auth._market = "se"
    auth._ntag = auth.NTAG_SENTINEL
    auth.KEEPALIVE_VERIFY_INTERVAL_SECONDS = original_verify_interval
    auth.KEEPALIVE_TOKEN_INTERVAL_SECONDS = original_token_interval


@pytest.fixture
def client():
    return NordnetClient(session_token=None, host="public.nordnet.se")


# A real page's window.__initialState__ is JS-string-escaped JSON (the
# outer assignment is itself a quoted string), so the interesting bits show
# up backslash-escaped in the raw HTML - this mirrors that shape rather
# than a plain, already-parsed JSON blob.
ROOT_PAGE_HTML = (
    '<html><body><script>window.__initialState__='
    '"{\\"meta\\":{\\"basename\\":\\"/\\",\\"clientId\\":\\"NEXT\\",'
    '\\"ntag\\":\\"cf5cd914-69f7-4df4-b8f6-5075b1ca85c7\\",\\"tld\\":\\"fi\\"}}"'
    "</script></body></html>"
)


@respx.mock
@pytest.mark.asyncio
async def test_start_order_returns_both_image_and_ascii_qr():
    # There's no reliable way to know in advance whether a given host will
    # actually render the MCP App view, so both are always sent - the image
    # for hosts that do, ASCII art text for hosts that don't (e.g. a plain
    # terminal like Claude Code).
    auth.configure(client=None, host="public.nordnet.fi")
    respx.get(f"{auth.API_HOST}/authentication/v1/methods/luna/start").mock(
        return_value=httpx.Response(
            200, json={"signingNonce": "sn-1", "loginNonce": "ln-1", "state": "SENT"}
        )
    )

    app = auth.register_tools(_FakeApp())
    result = await app.tools["nordnet_auth"]()

    assert len(result) == 3
    image = next(c for c in result if c.type == "image")
    assert image.mimeType == "image/png"
    text_blocks = [c for c in result if c.type == "text"]
    assert len(text_blocks) == 2
    # The view's own JS grabs the *first* text block as its JSON meta (see
    # auth_view.html's ontoolresult) - order matters, the meta must come
    # before the ASCII art, not after.
    meta, qr_text = text_blocks
    assert "startedAtMs" in json.loads(meta.text)
    assert qr_text.text.startswith("```")
    assert "█" in qr_text.text or "▄" in qr_text.text
    assert auth._pending["signing_nonce"] == "sn-1"
    assert auth._pending["login_nonce"] == "ln-1"


@respx.mock
@pytest.mark.asyncio
async def test_start_skips_qr_when_already_authenticated(client):
    # Second line of defense (first is the tool's own docstring telling the
    # model not to call this speculatively): even if it does get called
    # while a valid session already exists, no QR should ever be generated
    # or shown for it.
    client.session_token = "still-valid-token"
    auth.configure(client=client, host="public.nordnet.se")
    respx.post("https://www.nordnet.se/nnxapi/authorization/v1/tokens").mock(
        return_value=httpx.Response(200, json={"jwt": "irrelevant"})
    )
    respx.get("https://www.nordnet.se/nnxapi/authentication/v2/sessions/verify").mock(
        return_value=httpx.Response(200, json={"hasOnpremSession": True})
    )
    start_route = respx.get(f"{auth.API_HOST}/authentication/v1/methods/luna/start")

    app = auth.register_tools(_FakeApp())
    result = await app.tools["nordnet_auth"]()

    assert len(result) == 1
    assert result[0].type == "text"
    assert json.loads(result[0].text) == {"status": "already_authenticated"}
    assert auth._pending is None
    assert not start_route.called


@respx.mock
@pytest.mark.asyncio
async def test_poll_no_pending_order_and_no_token_reports_expired():
    auth.configure(client=None, host="public.nordnet.se")
    result = await auth._poll_once()
    assert result == {"status": "expired"}


@respx.mock
@pytest.mark.asyncio
async def test_poll_no_pending_order_but_valid_token_reports_already_authenticated(client):
    # Regression test: a view that gets re-mounted (chat reopened, server
    # restarted and reloaded a still-valid token from .env) must not be
    # told "expired", or it'll spin up a needless fresh QR for a session
    # that was never actually gone.
    client.session_token = "still-valid-token"
    auth.configure(client=client, host="public.nordnet.se")
    respx.post("https://www.nordnet.se/nnxapi/authorization/v1/tokens").mock(
        return_value=httpx.Response(200, json={"jwt": "irrelevant"})
    )
    respx.get("https://www.nordnet.se/nnxapi/authentication/v2/sessions/verify").mock(
        return_value=httpx.Response(200, json={"hasOnpremSession": True})
    )

    result = await auth._poll_once()

    assert result == {"status": "already_authenticated"}


@respx.mock
@pytest.mark.asyncio
async def test_poll_no_pending_order_but_dead_token_reports_expired(client):
    # A present token string isn't enough - Nordnet has to actually still
    # honor it. A stale/dead token (e.g. reloaded from .env long after it
    # expired) must not be reported as authenticated.
    client.session_token = "dead-token"
    auth.configure(client=client, host="public.nordnet.se")
    respx.post("https://www.nordnet.se/nnxapi/authorization/v1/tokens").mock(
        return_value=httpx.Response(200, json={"jwt": "irrelevant"})
    )
    respx.get("https://www.nordnet.se/nnxapi/authentication/v2/sessions/verify").mock(
        return_value=httpx.Response(200, json={"hasOnpremSession": False})
    )

    result = await auth._poll_once()

    assert result == {"status": "expired"}


@respx.mock
@pytest.mark.asyncio
async def test_has_valid_session_refreshes_authorization_token_first(client):
    nnapi_route = respx.post("https://www.nordnet.se/api/2/authentication/nnx-session/login").mock(
        return_value=httpx.Response(200)
    )
    token_route = respx.post("https://www.nordnet.se/nnxapi/authorization/v1/tokens").mock(
        return_value=httpx.Response(200, json={"jwt": "x"})
    )
    root_route = respx.get("https://www.nordnet.se/").mock(
        return_value=httpx.Response(200, text=ROOT_PAGE_HTML)
    )
    verify_route = respx.get("https://www.nordnet.se/nnxapi/authentication/v2/sessions/verify").mock(
        return_value=httpx.Response(200, json={"hasOnpremSession": True})
    )
    client.session_token = "tok"
    auth.configure(client=client, host="public.nordnet.se")

    result = await auth._has_valid_session()

    assert result is True
    assert nnapi_route.called
    assert token_route.called
    assert root_route.called
    assert auth._ntag == "cf5cd914-69f7-4df4-b8f6-5075b1ca85c7"
    assert verify_route.called
    assert verify_route.calls[0].request.headers["ntag"] == "cf5cd914-69f7-4df4-b8f6-5075b1ca85c7"


@respx.mock
@pytest.mark.asyncio
async def test_has_valid_session_still_checks_verify_if_bridge_refresh_fails(client):
    # Both bridge-refresh calls are best-effort: if they fail, still fall
    # through to verify() in case an earlier attempt already established
    # the bridge, rather than reporting invalid outright.
    respx.post("https://www.nordnet.se/api/2/authentication/nnx-session/login").mock(
        return_value=httpx.Response(500)
    )
    respx.post("https://www.nordnet.se/nnxapi/authorization/v1/tokens").mock(
        return_value=httpx.Response(500)
    )
    respx.get("https://www.nordnet.se/nnxapi/authentication/v2/sessions/verify").mock(
        return_value=httpx.Response(200, json={"hasOnpremSession": True})
    )
    client.session_token = "tok"
    auth.configure(client=client, host="public.nordnet.se")

    result = await auth._has_valid_session()

    assert result is True


@respx.mock
@pytest.mark.asyncio
async def test_create_nnapi_session_sends_client_id_and_cookie():
    # client-id: NEXT is the one header that actually matters here -
    # confirmed live, the real endpoint 500s without it regardless of
    # anything else about the request.
    auth.configure(client=None, host="public.nordnet.se")
    nnapi_route = respx.post("https://www.nordnet.se/api/2/authentication/nnx-session/login").mock(
        return_value=httpx.Response(200)
    )

    await auth._create_nnapi_session("tok-1")

    sent = nnapi_route.calls[0].request
    assert sent.headers["cookie"] == "NNX_SESSION_ID=tok-1"
    assert sent.headers["client-id"] == "NEXT"


@respx.mock
@pytest.mark.asyncio
async def test_create_nnapi_session_raises_on_failure():
    auth.configure(client=None, host="public.nordnet.se")
    respx.post("https://www.nordnet.se/api/2/authentication/nnx-session/login").mock(
        return_value=httpx.Response(500)
    )

    with pytest.raises(httpx.HTTPStatusError):
        await auth._create_nnapi_session("tok-1")


@respx.mock
@pytest.mark.asyncio
async def test_fetch_ntag_parses_meta_ntag_from_initial_state():
    auth.configure(client=None, host="public.nordnet.se")
    root_route = respx.get("https://www.nordnet.se/").mock(
        return_value=httpx.Response(200, text=ROOT_PAGE_HTML)
    )

    ntag = await auth._fetch_ntag("tok-1")

    assert ntag == "cf5cd914-69f7-4df4-b8f6-5075b1ca85c7"
    sent = root_route.calls[0].request
    assert sent.headers["cookie"] == "NNX_SESSION_ID=tok-1"
    assert sent.headers["client-id"] == "NEXT"


@respx.mock
@pytest.mark.asyncio
async def test_fetch_ntag_returns_none_when_not_found():
    auth.configure(client=None, host="public.nordnet.se")
    respx.get("https://www.nordnet.se/").mock(
        return_value=httpx.Response(200, text="<html>no initial state here</html>")
    )

    assert await auth._fetch_ntag("tok-1") is None


@respx.mock
@pytest.mark.asyncio
async def test_poll_pending_state_stays_pending():
    auth.configure(client=None, host="public.nordnet.se")
    auth._pending = {
        "signing_nonce": "sn-1",
        "login_nonce": "ln-1",
        "started_at": __import__("time").monotonic(),
    }
    respx.post(f"{auth.API_HOST}/authentication/v1/methods/luna/poll").mock(
        return_value=httpx.Response(200, json={"signingNonce": "sn-1", "state": "SENT"})
    )

    result = await auth._poll_once()

    assert result == {"status": "pending"}
    assert auth._pending is not None  # order still open


@respx.mock
@pytest.mark.asyncio
async def test_poll_signed_updates_client_token_in_memory(client):
    auth.configure(client=client, host="public.nordnet.se")
    auth._pending = {
        "signing_nonce": "sn-1",
        "login_nonce": "ln-1",
        "started_at": __import__("time").monotonic(),
    }

    respx.post(f"{auth.API_HOST}/authentication/v1/methods/luna/poll").mock(
        return_value=httpx.Response(200, json={"signingNonce": "sn-1", "state": "SIGNED"})
    )
    respx.get("https://www.nordnet.se/next-external/csrf").mock(
        return_value=httpx.Response(200, json={"csrf": "csrf-token"})
    )
    session_route = respx.post("https://www.nordnet.se/nnxapi/authentication/v2/sessions").mock(
        return_value=httpx.Response(
            200,
            headers=[("set-cookie", "NNX_SESSION_ID=new-token-123; Path=/; HttpOnly")],
        )
    )
    nnapi_route = respx.post("https://www.nordnet.se/api/2/authentication/nnx-session/login").mock(
        return_value=httpx.Response(200)
    )
    root_route = respx.get("https://www.nordnet.se/").mock(
        return_value=httpx.Response(200, text=ROOT_PAGE_HTML)
    )
    respx.get("https://www.nordnet.se/nnxapi/authentication/v2/sessions/verify").mock(
        return_value=httpx.Response(200, json={"hasOnpremSession": True})
    )

    result = await auth._poll_once()

    assert result == {"status": "signed"}
    assert client.session_token == "new-token-123"
    # A QR-obtained session was created by the NEXT web client, so the data
    # API needs the matching client-id header from now on.
    assert client.client_id == "NEXT"
    assert auth._pending is None
    sent = session_route.calls[0].request
    assert sent.headers["ntag"] == auth.NTAG_SENTINEL
    # The bridge-establishing call (see module docstring) must fire with
    # the freshly-obtained token, not the stale one that was in .env.
    assert nnapi_route.called
    assert nnapi_route.calls[0].request.headers["cookie"] == "NNX_SESSION_ID=new-token-123"
    # The real (non-sentinel) ntag must get picked up right after auth too.
    assert root_route.called
    assert auth._ntag == "cf5cd914-69f7-4df4-b8f6-5075b1ca85c7"


@respx.mock
@pytest.mark.asyncio
async def test_poll_signed_does_not_refetch_ntag_once_already_known(client):
    # ntag is fetched once per process and kept in memory (see module
    # docstring) - it's been observed identical across different tokens
    # from the same environment, so a second auth (or a re-mounted view
    # after a restart that still has it in memory) must not pay for another
    # full page load just to get the same value again.
    auth.configure(client=client, host="public.nordnet.se")
    auth._ntag = "already-known-ntag"
    auth._pending = {
        "signing_nonce": "sn-1",
        "login_nonce": "ln-1",
        "started_at": __import__("time").monotonic(),
    }

    respx.post(f"{auth.API_HOST}/authentication/v1/methods/luna/poll").mock(
        return_value=httpx.Response(200, json={"signingNonce": "sn-1", "state": "SIGNED"})
    )
    respx.get("https://www.nordnet.se/next-external/csrf").mock(
        return_value=httpx.Response(200, json={"csrf": "csrf-token"})
    )
    respx.post("https://www.nordnet.se/nnxapi/authentication/v2/sessions").mock(
        return_value=httpx.Response(
            200,
            headers=[("set-cookie", "NNX_SESSION_ID=new-token-456; Path=/; HttpOnly")],
        )
    )
    respx.post("https://www.nordnet.se/api/2/authentication/nnx-session/login").mock(
        return_value=httpx.Response(200)
    )
    respx.get("https://www.nordnet.se/nnxapi/authentication/v2/sessions/verify").mock(
        return_value=httpx.Response(200, json={"hasOnpremSession": True})
    )
    root_route = respx.get("https://www.nordnet.se/")

    result = await auth._poll_once()

    assert result == {"status": "signed"}
    assert not root_route.called
    assert auth._ntag == "already-known-ntag"


@respx.mock
@pytest.mark.asyncio
async def test_poll_session_creation_failure_reports_error():
    auth.configure(client=None, host="public.nordnet.se")
    auth._pending = {
        "signing_nonce": "sn-1",
        "login_nonce": "ln-1",
        "started_at": __import__("time").monotonic(),
    }

    respx.post(f"{auth.API_HOST}/authentication/v1/methods/luna/poll").mock(
        return_value=httpx.Response(200, json={"signingNonce": "sn-1", "state": "SIGNED"})
    )
    respx.get("https://www.nordnet.se/next-external/csrf").mock(
        return_value=httpx.Response(200, json={"csrf": "csrf-token"})
    )
    respx.post("https://www.nordnet.se/nnxapi/authentication/v2/sessions").mock(
        return_value=httpx.Response(401)
    )

    result = await auth._poll_once()

    assert result["status"] == "error"
    assert "401" in result["message"]


@pytest.mark.asyncio
async def test_poll_expired_order_is_cleared():
    auth.configure(client=None, host="public.nordnet.se")
    auth._pending = {
        "signing_nonce": "sn-1",
        "login_nonce": "ln-1",
        "started_at": __import__("time").monotonic() - auth.ORDER_TTL_SECONDS - 1,
    }

    result = await auth._poll_once()

    assert result == {"status": "expired"}
    assert auth._pending is None


@respx.mock
@pytest.mark.asyncio
async def test_rotation_completes_login_approved_just_before_rotation(client):
    # Regression test: the view rotates on the same 100s window as the
    # order's local TTL, so by the time a rotation request arrives the TTL
    # has typically just expired. An approval that landed moments earlier
    # must still be picked up - the rotation path has to genuinely ask
    # Nordnet about the outgoing order (ignoring the local clock), not
    # short-circuit to "expired" and strand the approval.
    auth.configure(client=client, host="public.nordnet.se")
    auth._pending = {
        "signing_nonce": "sn-old",
        "login_nonce": "ln-old",
        "started_at": __import__("time").monotonic() - auth.ORDER_TTL_SECONDS - 1,
    }
    respx.post(f"{auth.API_HOST}/authentication/v1/methods/luna/poll").mock(
        return_value=httpx.Response(200, json={"signingNonce": "sn-old", "state": "SIGNED"})
    )
    respx.get("https://www.nordnet.se/next-external/csrf").mock(
        return_value=httpx.Response(200, json={"csrf": "csrf-token"})
    )
    respx.post("https://www.nordnet.se/nnxapi/authentication/v2/sessions").mock(
        return_value=httpx.Response(
            200,
            headers=[("set-cookie", "NNX_SESSION_ID=rotated-token; Path=/; HttpOnly")],
        )
    )
    respx.post("https://www.nordnet.se/api/2/authentication/nnx-session/login").mock(
        return_value=httpx.Response(200)
    )
    respx.get("https://www.nordnet.se/").mock(
        return_value=httpx.Response(200, text=ROOT_PAGE_HTML)
    )
    respx.get("https://www.nordnet.se/nnxapi/authentication/v2/sessions/verify").mock(
        return_value=httpx.Response(200, json={"hasOnpremSession": True})
    )
    start_route = respx.get(f"{auth.API_HOST}/authentication/v1/methods/luna/start")

    app = auth.register_tools(_FakeApp())
    result = await app.tools["nordnet_auth"]()

    assert len(result) == 1
    assert json.loads(result[0].text) == {"status": "already_authenticated"}
    assert client.session_token == "rotated-token"
    assert not start_route.called  # no needless new QR for a finished login


@respx.mock
@pytest.mark.asyncio
async def test_rotation_replaces_order_when_still_unsigned(client):
    auth.configure(client=client, host="public.nordnet.se")
    auth._pending = {
        "signing_nonce": "sn-old",
        "login_nonce": "ln-old",
        "started_at": __import__("time").monotonic() - auth.ORDER_TTL_SECONDS - 1,
    }
    respx.post(f"{auth.API_HOST}/authentication/v1/methods/luna/poll").mock(
        return_value=httpx.Response(200, json={"signingNonce": "sn-old", "state": "SENT"})
    )
    respx.get(f"{auth.API_HOST}/authentication/v1/methods/luna/start").mock(
        return_value=httpx.Response(
            200, json={"signingNonce": "sn-new", "loginNonce": "ln-new", "state": "SENT"}
        )
    )

    app = auth.register_tools(_FakeApp())
    result = await app.tools["nordnet_auth"]()

    assert len(result) == 3  # a fresh QR: image + meta + ascii
    assert auth._pending["signing_nonce"] == "sn-new"
    assert auth._pending["login_nonce"] == "ln-new"


@respx.mock
@pytest.mark.asyncio
async def test_poll_signed_bridge_failure_surfaces_error(client):
    # Regression test: a failed nnx-session bridge call used to be
    # swallowed and the login still reported as "signed", leaving the user
    # with a token the data API rejects. It must surface as an error when
    # verify() confirms the session isn't actually working.
    auth.configure(client=client, host="public.nordnet.se")
    auth._pending = {
        "signing_nonce": "sn-1",
        "login_nonce": "ln-1",
        "started_at": __import__("time").monotonic(),
    }
    respx.post(f"{auth.API_HOST}/authentication/v1/methods/luna/poll").mock(
        return_value=httpx.Response(200, json={"signingNonce": "sn-1", "state": "SIGNED"})
    )
    respx.get("https://www.nordnet.se/next-external/csrf").mock(
        return_value=httpx.Response(200, json={"csrf": "csrf-token"})
    )
    respx.post("https://www.nordnet.se/nnxapi/authentication/v2/sessions").mock(
        return_value=httpx.Response(
            200,
            headers=[("set-cookie", "NNX_SESSION_ID=broken-token; Path=/; HttpOnly")],
        )
    )
    respx.post("https://www.nordnet.se/api/2/authentication/nnx-session/login").mock(
        return_value=httpx.Response(500)
    )
    respx.get("https://www.nordnet.se/").mock(
        return_value=httpx.Response(200, text=ROOT_PAGE_HTML)
    )
    respx.get("https://www.nordnet.se/nnxapi/authentication/v2/sessions/verify").mock(
        return_value=httpx.Response(200, json={"hasOnpremSession": False})
    )

    result = await auth._poll_once()

    assert result["status"] == "error"
    assert "bridge call failed" in result["message"]
    assert client.session_token is None  # never handed a token that doesn't work
    assert auth._pending is None


@respx.mock
@pytest.mark.asyncio
async def test_poll_signed_but_verify_false_surfaces_error(client):
    # Even with the bridge call succeeding, "signed" is only reported once
    # verify() confirms Nordnet actually considers the session working.
    auth.configure(client=client, host="public.nordnet.se")
    auth._pending = {
        "signing_nonce": "sn-1",
        "login_nonce": "ln-1",
        "started_at": __import__("time").monotonic(),
    }
    respx.post(f"{auth.API_HOST}/authentication/v1/methods/luna/poll").mock(
        return_value=httpx.Response(200, json={"signingNonce": "sn-1", "state": "SIGNED"})
    )
    respx.get("https://www.nordnet.se/next-external/csrf").mock(
        return_value=httpx.Response(200, json={"csrf": "csrf-token"})
    )
    respx.post("https://www.nordnet.se/nnxapi/authentication/v2/sessions").mock(
        return_value=httpx.Response(
            200,
            headers=[("set-cookie", "NNX_SESSION_ID=half-token; Path=/; HttpOnly")],
        )
    )
    respx.post("https://www.nordnet.se/api/2/authentication/nnx-session/login").mock(
        return_value=httpx.Response(200)
    )
    respx.get("https://www.nordnet.se/").mock(
        return_value=httpx.Response(200, text=ROOT_PAGE_HTML)
    )
    respx.get("https://www.nordnet.se/nnxapi/authentication/v2/sessions/verify").mock(
        return_value=httpx.Response(200, json={"hasOnpremSession": False})
    )

    result = await auth._poll_once()

    assert result["status"] == "error"
    assert "working session" in result["message"]
    assert "bridge call failed" not in result["message"]
    assert client.session_token is None


VERIFY_URL = "https://www.nordnet.se/nnxapi/authentication/v2/sessions/verify"


@respx.mock
@pytest.mark.asyncio
async def test_keepalive_verify_pings_periodically_while_running(client):
    route = respx.get(VERIFY_URL).mock(return_value=httpx.Response(200))
    client.session_token = "tok"
    auth.configure(client=client, host="public.nordnet.se")
    auth.KEEPALIVE_VERIFY_INTERVAL_SECONDS = 0.03

    async with auth.lifespan(None):
        await asyncio.sleep(0.4)

    assert route.call_count >= 3
    sent = route.calls[0].request
    assert sent.headers["client-id"] == "NEXT"
    assert "ntag" in sent.headers
    assert sent.headers["cookie"] == "NNX_SESSION_ID=tok"


@respx.mock
@pytest.mark.asyncio
async def test_keepalive_stops_after_lifespan_exits(client):
    route = respx.get(VERIFY_URL).mock(return_value=httpx.Response(200))
    client.session_token = "tok"
    auth.configure(client=client, host="public.nordnet.se")
    auth.KEEPALIVE_VERIFY_INTERVAL_SECONDS = 0.05

    async with auth.lifespan(None):
        await asyncio.sleep(0.12)
    count_at_exit = route.call_count

    await asyncio.sleep(0.15)

    assert route.call_count == count_at_exit


@respx.mock
@pytest.mark.asyncio
async def test_keepalive_skips_when_no_token(client):
    route = respx.get(VERIFY_URL).mock(return_value=httpx.Response(200))
    assert client.session_token is None
    auth.configure(client=client, host="public.nordnet.se")
    auth.KEEPALIVE_VERIFY_INTERVAL_SECONDS = 0.05

    async with auth.lifespan(None):
        await asyncio.sleep(0.12)

    assert route.call_count == 0


@respx.mock
@pytest.mark.asyncio
async def test_keepalive_swallows_expired_session_error(client):
    respx.get(VERIFY_URL).mock(return_value=httpx.Response(401))
    client.session_token = "tok"
    auth.configure(client=client, host="public.nordnet.se")
    auth.KEEPALIVE_VERIFY_INTERVAL_SECONDS = 0.05

    async with auth.lifespan(None):
        await asyncio.sleep(0.12)
    # No assertion needed beyond "didn't raise" - a background heartbeat
    # failure must never crash the server.


TOKENS_URL = "https://www.nordnet.se/nnxapi/authorization/v1/tokens"


@respx.mock
@pytest.mark.asyncio
async def test_keepalive_token_refreshes_periodically_while_running(client):
    verify_route = respx.get(VERIFY_URL).mock(return_value=httpx.Response(200))
    token_route = respx.post(TOKENS_URL).mock(return_value=httpx.Response(200, json={"jwt": "x"}))
    client.session_token = "tok"
    auth.configure(client=client, host="public.nordnet.se")
    # Keep verify() effectively idle so only the token loop's cadence is
    # under test here.
    auth.KEEPALIVE_VERIFY_INTERVAL_SECONDS = 1000
    auth.KEEPALIVE_TOKEN_INTERVAL_SECONDS = 0.03

    async with auth.lifespan(None):
        await asyncio.sleep(0.4)

    assert token_route.call_count >= 3
    assert verify_route.call_count == 0
    sent = token_route.calls[0].request
    assert sent.headers["cookie"] == "NNX_SESSION_ID=tok"
    assert sent.headers["origin"] == "https://www.nordnet.se"


@respx.mock
@pytest.mark.asyncio
async def test_keepalive_token_stops_after_lifespan_exits(client):
    token_route = respx.post(TOKENS_URL).mock(return_value=httpx.Response(200, json={"jwt": "x"}))
    client.session_token = "tok"
    auth.configure(client=client, host="public.nordnet.se")
    auth.KEEPALIVE_VERIFY_INTERVAL_SECONDS = 1000
    auth.KEEPALIVE_TOKEN_INTERVAL_SECONDS = 0.05

    async with auth.lifespan(None):
        await asyncio.sleep(0.12)
    count_at_exit = token_route.call_count

    await asyncio.sleep(0.15)

    assert token_route.call_count == count_at_exit


def test_view_resource_registers_ui_html():
    resources = {}

    class FakeApp:
        def resource(self, uri, **kwargs):
            def decorator(fn):
                resources[uri] = (fn, kwargs)
                return fn
            return decorator

    auth.register_resources(FakeApp())

    fn, kwargs = resources[auth.VIEW_URI]
    assert kwargs["mime_type"] == "text/html;profile=mcp-app"
    # The SDK is vendored into the HTML - the view must need no external
    # resource domains at all.
    assert kwargs["meta"]["ui"]["csp"]["resourceDomains"] == []
    html = fn()
    assert "<html>" in html
    assert "MCPExtApps" in html
    assert "unpkg.com" not in html


class _FakeApp:
    """Minimal stand-in for FastMCP's app.tool() decorator, just enough to
    capture and call the registered tool functions directly in tests."""

    def __init__(self):
        self.tools = {}

    def tool(self, **_kwargs):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn
        return decorator
