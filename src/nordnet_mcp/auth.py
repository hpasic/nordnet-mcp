"""QR-code login (MCP App) and session keep-alive for Nordnet.

Reproduces the same "LUNA" login flow used at nordnet.<market>/login:
start a login order, show a QR code, poll until the user approves it in
the Nordnet mobile app, then exchange the approved order for a session
cookie (NNX_SESSION_ID). This lets the server obtain its own session
without the user ever touching browser DevTools.

A token obtained this way is kept in memory only (applied to the running
NordnetClient immediately) - it is not written to .env or anywhere else on
disk. If the server process restarts for any reason, the token is gone and
logging in again is required. Writing the token back to .env would avoid
that, but at a security cost (a bearer token sitting in a plaintext file)
that isn't worth it unless this turns out to be too disruptive in practice
- that's the tradeoff to revisit if so.

The QR/poll/session endpoints below were reverse-engineered from Nordnet's
own login page JS bundles (fetched and read directly, not guessed) and
verified live; there is no official API for this.

Establishing the session actually takes *two* calls, not one - this was
the root cause of logins silently not working (verify() kept reporting
`hasOnpremSession: false` right after a successful-looking login). Reading
Nordnet's own login-page bundle (`nn-vendors-...page-login...js`) shows
that right after `POST /nnxapi/authentication/v2/sessions` succeeds (the
call that sets the NNX_SESSION_ID cookie), the frontend immediately calls a
function literally named `createNnapiSessionAndDecideRoute`, which does
`POST /api/2/authentication/nnx-session/login`. That second call is what
actually creates the "on-prem" (NNX) backend session bridge that verify()'s
`hasOnpremSession` field reports on - the first call alone only gets you
the cookie, not a working on-prem session. See `_create_nnapi_session()`
below.

Both this call and the actual data API calls in client.py turned out to
need one more thing that isn't obvious from the JS (it's set on a shared
API client instance, not visible per-call): a `client-id: NEXT` header.
Confirmed against the live API with a real freshly-scanned token - without
it, `nnx-session/login` 500s unconditionally (regardless of an otherwise
byte-for-byte correct request) and the data API 401s even for an otherwise
valid, bridged session. This was found by diffing a real captured browser
request for this exact call against what was being sent here. This is
confirmed working end-to-end, not a theory: `_create_nnapi_session()`
followed by `verify()` was observed flipping `hasOnpremSession` from
`false` to `true` for the same token, and the data API accepting that same
token immediately after.

The `ntag` header sent on `verify()` (and other authenticated requests)
also isn't a random per-request value, despite an earlier version of this
code generating one with `uuid.uuid4()` - a real browser gets a stable
value once from `meta.ntag` inside `window.__initialState__`, embedded in
the root page's HTML on every full page load, and reuses that same value
for every subsequent request. `NTAG_SENTINEL` ("NO_NTAG_RECEIVED_YET") is
what's sent before a real one has ever been fetched - matches what a real
browser sends on the very first authenticated request too. See
`_fetch_ntag()`.

Separately, keeping an *established* session alive appears to need two
periodic calls, matching what a real logged-in browser tab's own network
traffic was observed doing: `GET /nnxapi/authentication/v2/sessions/verify`
every ~20s, and `POST /nnxapi/authorization/v1/tokens` every ~60s (both
cookie-authenticated, on the www.nordnet.<market> host). Unlike the
nnx-session/login call above, this `tokens` endpoint was not found being
called anywhere in the login-page bundle - it's included here purely
because it was observed in real browser network traffic at that cadence,
so its exact purpose is still a theory: based on the observed cadence and
that verify()'s response field is named `hasOnpremSession`, it may refresh
the same on-prem bridge rather than establish it from scratch. `lifespan()`
below reproduces both calls at their observed cadence while the server is
running, so the session keeps sliding forward instead of expiring from
inactivity.
"""
import asyncio
import base64
import contextlib
import importlib.resources
import io
import json
import re
import time
from contextlib import asynccontextmanager

import httpx
import qrcode
from mcp import types

API_HOST = "https://api.prod.nntech.io"
VIEW_URI = "ui://nordnet-mcp/auth"
ORDER_TTL_SECONDS = 100  # matches Nordnet's own web login timeout
KEEPALIVE_VERIFY_INTERVAL_SECONDS = 20  # matches observed browser cadence
KEEPALIVE_TOKEN_INTERVAL_SECONDS = 60  # matches observed browser cadence
NTAG_SENTINEL = "NO_NTAG_RECEIVED_YET"  # what a real browser sends before it has one
NTAG_RE = re.compile(r'\\"ntag\\":\\"([0-9a-fA-F-]+)\\"')

_client = None  # NordnetClient - session_token is mutated in place on success
_market = "se"
_pending: dict | None = None
_ntag = NTAG_SENTINEL


def configure(client, host: str = "public.nordnet.se"):
    global _client, _market, _ntag
    _client = client
    _market = host.rsplit(".", 1)[-1].lower()
    _ntag = NTAG_SENTINEL


def _qr_image_content(url: str) -> types.ImageContent:
    qr = qrcode.QRCode(border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    b64 = base64.b64encode(buffer.getvalue()).decode()
    return types.ImageContent(type="image", data=b64, mimeType="image/png")


def _qr_ascii_content(url: str) -> types.TextContent:
    """Unicode half-block rendering of the QR, alongside the image (see
    nordnet_auth) - for clients that don't render the ui://... MCP App view,
    where the image would otherwise be useless (e.g. a plain terminal like
    Claude Code can't display it at all). There's no reliable way for the
    server to tell in advance whether a given client will actually render
    the view, so both are always sent and the model is left to notice which
    one the user can actually see. Wrapped in a code fence so Markdown
    renderers keep it monospace, which the blocks need to line up into a
    scannable pattern."""
    qr = qrcode.QRCode(border=2)
    qr.add_data(url)
    qr.make(fit=True)
    buffer = io.StringIO()
    qr.print_ascii(out=buffer, invert=True)
    return types.TextContent(type="text", text=f"```\n{buffer.getvalue()}\n```")


async def _start_order() -> dict:
    global _pending

    async with httpx.AsyncClient() as http:
        resp = await http.get(f"{API_HOST}/authentication/v1/methods/luna/start")
        resp.raise_for_status()
        data = resp.json()

    _pending = {
        "signing_nonce": data["signingNonce"],
        "login_nonce": data["loginNonce"],
        "started_at": time.monotonic(),
    }
    return _pending


async def _poll_once() -> dict:
    global _pending, _ntag

    if _pending is None:
        # No order in flight. If a session already exists - e.g. a manually
        # configured NORDNET_SESSION_TOKEN in .env, still valid at startup -
        # there's nothing to do. Report it as already authenticated rather
        # than "expired", so a re-mounted
        # view doesn't spin up a needless fresh QR for a session that was
        # never actually gone. Distinct from "signed": nothing new just
        # happened here, so the view shouldn't nudge the conversation
        # forward with sendMessage() the way a real login completion does.
        # A real verify() call, not just "is a token string set" - a
        # present-but-dead token shouldn't report as authenticated.
        if await _has_valid_session():
            return {"status": "already_authenticated"}
        return {"status": "expired"}
    if time.monotonic() - _pending["started_at"] > ORDER_TTL_SECONDS:
        _pending = None
        return {"status": "expired"}

    www_host = f"https://www.nordnet.{_market}"

    async with httpx.AsyncClient() as http:
        poll_resp = await http.post(
            f"{API_HOST}/authentication/v1/methods/luna/poll",
            json={"signingNonce": _pending["signing_nonce"]},
        )
        poll_resp.raise_for_status()
        if poll_resp.json().get("state") != "SIGNED":
            return {"status": "pending"}

        # Warms the shared client's cookie jar with the _csrf cookie this
        # sets - carried automatically into the session-creation POST below
        # since both calls share the same httpx.AsyncClient.
        csrf_resp = await http.get(f"{www_host}/next-external/csrf")
        csrf_resp.raise_for_status()

        session_resp = await http.post(
            f"{www_host}/nnxapi/authentication/v2/sessions",
            headers={"ntag": _ntag},
            json={
                "authenticationProvider": "LUNA",
                "countryCode": _market.upper(),
                "luna": {
                    "signingNonce": _pending["signing_nonce"],
                    "loginNonce": _pending["login_nonce"],
                },
            },
        )
        if session_resp.status_code not in (200, 204):
            return {
                "status": "error",
                "message": f"Session creation failed with HTTP {session_resp.status_code}",
            }
        token = session_resp.cookies.get("NNX_SESSION_ID")

    if not token:
        return {"status": "error", "message": "No NNX_SESSION_ID cookie in response"}

    # The NNX_SESSION_ID cookie alone isn't a working on-prem session yet -
    # see module docstring. Best-effort and non-fatal, matching what
    # Nordnet's own frontend does right here (it logs and still proceeds
    # even if this fails).
    with contextlib.suppress(Exception):
        await _create_nnapi_session(token)
        # ntag is fetched once per process and kept in memory (same guard
        # as _has_valid_session) - not re-fetched on every login. It's been
        # observed identical across different tokens from the same
        # environment, so it isn't tied to the specific login, and each
        # fetch costs a full ~900KB page load.
        if _ntag == NTAG_SENTINEL:
            fetched_ntag = await _fetch_ntag(token)
            if fetched_ntag:
                _ntag = fetched_ntag

    if _client is not None:
        _client.session_token = token

    _pending = None
    return {"status": "signed"}


def register_tools(app):

    @app.tool(meta={"ui": {"resourceUri": VIEW_URI}})
    async def nordnet_auth() -> list[types.ImageContent | types.TextContent]:
        """Start (or restart) Nordnet login via QR code.

        Do not call this speculatively "just in case" - a saved session may
        already be valid, and calling this anyway would show the user a
        disruptive QR code for nothing. Always try the actual requested
        Nordnet action first; only call this reactively, after it fails
        with a session-expired error, or when the user explicitly asks to
        log in or refresh their Nordnet session. Shows a QR code the user
        scans with the Nordnet mobile app; once they approve it there, the
        session is established and saved automatically — no token needs to
        be copied by hand. (As a second line of defense against ever
        showing a QR unnecessarily, this also verifies the current session
        live before generating a new one, and skips straight to reporting
        success if it's already valid.)

        Returns the same QR code twice, as both an image and ASCII art text
        - there's no reliable way to know in advance whether your host will
        render the MCP App view (in which case the image renders inline and
        no further action is needed, since the view polls on its own) or
        not (some hosts, e.g. Claude Code, don't render tool results
        directly to the user at all - only the ASCII art is visible there).
        Check which one the user can actually see. If it's the ASCII art,
        you MUST paste that exact text block, unmodified (it's already
        wrapped in its own code fence - do not add another), into your
        reply, or the user has no way to see or scan the code - and in that
        case there is no automatic polling either, so once the user tells
        you they've scanned and approved it, call the `nordnet_login_poll`
        tool yourself to actually finish the login (see its own
        description).
        """
        if await _has_valid_session():
            return [types.TextContent(type="text", text=json.dumps({"status": "already_authenticated"}))]
        if _pending is not None:
            # The view now rotates on the same ORDER_TTL_SECONDS=100s window
            # as the underlying Nordnet order, but a rotation request can
            # still land right as that window closes - if the user scanned
            # and approved it just before then, overwriting `_pending` here
            # would silently strand that approval. Give it one last poll
            # before replacing it.
            result = await _poll_once()
            if result["status"] == "signed":
                return [types.TextContent(type="text", text=json.dumps({"status": "already_authenticated"}))]
        order = await _start_order()
        qr_url = f"https://www.nordnet.{_market}/login/web/app?signing_nonce={order['signing_nonce']}"
        # Wall-clock timestamp alongside the image, separate from the
        # monotonic one used for the server's own TTL bookkeeping in
        # _pending: this one travels with the content itself so the view
        # can tell how old *this particular result* actually is, even if
        # what it's looking at is a stale replay (e.g. the host re-showing
        # this app after the conversation was reopened long after the
        # original request) rather than a fresh call.
        meta = types.TextContent(type="text", text=json.dumps({"startedAtMs": int(time.time() * 1000)}))
        # Order matters: the view's own JS grabs the *first* text block as
        # its JSON meta (see auth_view.html's ontoolresult) - the ASCII art
        # text must come after it, not before, or that parsing breaks.
        return [_qr_image_content(qr_url), meta, _qr_ascii_content(qr_url)]

    @app.tool(meta={"ui": {"resourceUri": VIEW_URI, "visibility": ["app"]}})
    async def nordnet_login_poll() -> list[types.TextContent]:
        """Check the pending Nordnet login order and, once approved,
        finish it.

        Normally driven by the QR view itself on hosts that render MCP
        Apps - you don't need to call this yourself there. But on hosts
        without MCP Apps support, where `nordnet_auth` returned an ASCII QR
        instead, nothing polls automatically: after the user tells you
        they've scanned and approved it, call this tool yourself to
        actually complete the login.

        Returns a status field: "signed" (done - the session is now live),
        "pending" (not approved yet - if the user says they've scanned it,
        wait a couple seconds and call this again; a few retries is normal
        since approval on their phone isn't instant), "expired" (the QR
        timed out - call `nordnet_auth` again for a fresh one),
        "already_authenticated" (nothing was pending; a valid session
        already existed), or "error" (something went wrong; see the
        message field).
        """
        result = await _poll_once()
        return [types.TextContent(type="text", text=json.dumps(result))]

    return app


async def _verify_session(token: str) -> bool:
    """Same request a logged-in browser tab makes periodically to check
    it's still signed in — this is what actually resets Nordnet's idle
    timeout, not activity against the separate portfolio data API. Also
    doubles as the authoritative "is this token still good?" check: it
    returns 200 with hasOnpremSession true/false rather than erroring on a
    dead token, so the boolean in the body - not the HTTP status - is what
    actually answers that question."""
    async with httpx.AsyncClient() as http:
        resp = await http.get(
            f"https://www.nordnet.{_market}/nnxapi/authentication/v2/sessions/verify",
            headers={
                "Accept": "application/json",
                "client-id": "NEXT",
                "ntag": _ntag,
                "Referer": f"https://www.nordnet.{_market}/",
                "User-Agent": "Mozilla/5.0",
                "Cookie": f"NNX_SESSION_ID={token}",
            },
        )
        resp.raise_for_status()
        return bool(resp.json().get("hasOnpremSession"))


async def _fetch_ntag(token: str) -> str | None:
    """`ntag` isn't a random per-request value - a real browser gets it once
    from `meta.ntag` inside `window.__initialState__`, embedded in the root
    page's HTML on every full page load, and reuses it for every
    authenticated request after that (NTAG_SENTINEL is what's sent before
    this has ever been fetched, e.g. by _create_nnapi_session during login
    itself). Confirmed live: this value is identical to verify()'s own
    `onpremCsrfToken` field, but fetching it this way matches what the real
    frontend actually does rather than relying on that coincidence.

    This function itself doesn't cache anything - callers are responsible
    for only calling it once per process and keeping the result in `_ntag`
    (both call sites guard on `_ntag == NTAG_SENTINEL` first), since it's
    been observed identical across different tokens from the same
    environment (not tied to the specific login) and each call costs a full
    page load."""
    www_host = f"https://www.nordnet.{_market}"
    async with httpx.AsyncClient(follow_redirects=True) as http:
        resp = await http.get(
            f"{www_host}/",
            headers={
                "Accept": "text/html",
                "client-id": "NEXT",
                "User-Agent": "Mozilla/5.0",
                "Cookie": f"NNX_SESSION_ID={token}",
            },
        )
        resp.raise_for_status()
        match = NTAG_RE.search(resp.text)
        return match.group(1) if match else None


async def _create_nnapi_session(token: str) -> None:
    """Creates the actual "on-prem" (NNX) backend session bridge - the step
    verify()'s hasOnpremSession field reports on. Reverse-engineered from
    Nordnet's own login-page JS bundle: this is what its
    `createNnapiSessionAndDecideRoute` function calls, immediately after the
    LUNA session POST that sets NNX_SESSION_ID succeeds (see module
    docstring for how this was found).

    The one header that actually matters here - confirmed against the live
    API, this 500s without it regardless of anything else about the request
    - is `client-id: NEXT`."""
    www_host = f"https://www.nordnet.{_market}"
    async with httpx.AsyncClient() as http:
        resp = await http.post(
            f"{www_host}/api/2/authentication/nnx-session/login",
            headers={
                "Accept": "application/json",
                "client-id": "NEXT",
                "Content-Type": "application/json",
                "ntag": NTAG_SENTINEL,
                "Origin": www_host,
                "Referer": f"{www_host}/",
                "User-Agent": "Mozilla/5.0",
                "Cookie": f"NNX_SESSION_ID={token}",
            },
            json={},
        )
        resp.raise_for_status()


async def _create_authorization_token(token: str) -> None:
    """Periodic keepalive call only - not the bridge-establishing one (see
    module docstring and `_create_nnapi_session`, which is). A real
    logged-in browser tab calls this every ~60s, alongside verify() every
    ~20s, but it does not appear in the login-page bundle's own code path,
    so its exact purpose is still a theory. The JWT this returns isn't used
    for anything here; only the side effect of calling it matters, if the
    theory holds."""
    async with httpx.AsyncClient() as http:
        await http.post(
            f"https://www.nordnet.{_market}/nnxapi/authorization/v1/tokens",
            headers={
                "Accept": "*/*",
                "Content-Type": "application/json",
                "Origin": f"https://www.nordnet.{_market}",
                "Referer": f"https://www.nordnet.{_market}/",
                "User-Agent": "Mozilla/5.0",
                "Cookie": f"NNX_SESSION_ID={token}",
            },
            json={},
        )


async def _has_valid_session() -> bool:
    """Whether the current in-memory token is a session Nordnet still
    honors right now - not just whether a token string happens to be set,
    since a present-but-dead token (e.g. reloaded from .env after the
    server was idle a while) would pass a mere truthiness check."""
    global _ntag
    if _client is None or not _client.session_token:
        return False
    token = _client.session_token
    try:
        # Re-establish the on-prem bridge first, in case an earlier attempt
        # (e.g. right after login) failed or was never made (older token
        # loaded from .env, saved before this existed) - if this fails,
        # still fall through to verify() in case a bridge already exists.
        with contextlib.suppress(Exception):
            await _create_nnapi_session(token)
        with contextlib.suppress(Exception):
            await _create_authorization_token(token)
        # Same reasoning as above: an older/reloaded token may predate
        # ever having fetched a real ntag (e.g. a fresh server start that
        # loaded a still-valid token from .env), so still only the
        # sentinel. Fetch it once, not on every check - it's a session-
        # scoped identifier, not a per-request one.
        if _ntag == NTAG_SENTINEL:
            with contextlib.suppress(Exception):
                fetched_ntag = await _fetch_ntag(token)
                if fetched_ntag:
                    _ntag = fetched_ntag
        return await _verify_session(token)
    except Exception:
        return False


async def _keepalive_verify_loop():
    while True:
        await asyncio.sleep(KEEPALIVE_VERIFY_INTERVAL_SECONDS)
        token = _client.session_token if _client is not None else None
        if not token:
            continue
        with contextlib.suppress(Exception):
            # This endpoint returns 200 with {"hasOnpremSession": false}
            # rather than erroring even for an already-dead session, so
            # there's no status to react to here - the point is just to
            # touch the session and reset its idle timer. If it really has
            # expired, the next real tool call hits a 401 against the data
            # API and surfaces SessionExpiredError as usual. suppress()
            # here is only for network-level failures (timeouts, DNS, ...).
            await _verify_session(token)


async def _keepalive_token_loop():
    while True:
        await asyncio.sleep(KEEPALIVE_TOKEN_INTERVAL_SECONDS)
        token = _client.session_token if _client is not None else None
        if not token:
            continue
        with contextlib.suppress(Exception):
            await _create_authorization_token(token)


@asynccontextmanager
async def lifespan(app):
    """Keeps the Nordnet session alive for as long as the server runs, by
    reproducing the same two periodic calls a real logged-in browser tab
    makes (see module docstring)."""
    tasks = [
        asyncio.create_task(_keepalive_verify_loop()),
        asyncio.create_task(_keepalive_token_loop()),
    ]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task


EMBEDDED_VIEW_HTML = (importlib.resources.files(__package__) / "auth_view.html").read_text(encoding="utf-8")


def register_resources(app):
    @app.resource(
        VIEW_URI,
        mime_type="text/html;profile=mcp-app",
        meta={"ui": {"csp": {"resourceDomains": ["https://unpkg.com"]}}},
    )
    def view() -> str:
        """Login QR view HTML."""
        return EMBEDDED_VIEW_HTML

    return app
