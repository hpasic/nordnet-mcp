# nordnet-mcp

Read-only MCP server for Nordnet portfolio data.

It exposes Nordnet accounts, positions, trades, balances, and instrument lookup/search to MCP-compatible clients such as Claude Code, Claude Desktop, and Cursor.

Important:
- This project is unofficial and is not affiliated with, endorsed by, or supported by Nordnet.
- It uses your existing Nordnet session, established either via QR login (below) or a browser session token.
- Nordnet sessions are short-lived (a couple of hours) if left idle — but the server
  keeps its session alive on its own for as long as it keeps running (see Status).
- Never commit your token or share your `.env` file.

## Status

The server can now log itself in: call the `nordnet_auth` tool (or just try any
Nordnet tool — a 401 tells the model to call it) and a QR code appears in the
conversation. Scan it with the Nordnet mobile app and the session is established
automatically — no browser DevTools, no copy-pasting a cookie. `NORDNET_HOST`
must match the market your Nordnet app is registered in (e.g.
`public.nordnet.no` for a Norwegian account): the QR encodes a login link on
that market's domain, and an app from another market silently ignores the scan. In MCP Apps-capable
hosts this renders as a live QR image that rotates itself every 100 seconds (up to 3
codes / 5 minutes) if you haven't scanned it yet, and once approved it disappears and
sends a follow-up message so you don't have to retype what you originally asked.
Whether that finishes the loop entirely or just saves you the retyping depends on the
host: some submit it immediately, others (observed: Claude Desktop) pre-fill it in the
composer and leave sending it to you — in the latter case, a single Enter is all that's
left. On hosts without MCP Apps support (e.g. Claude Code), the QR is shown as ASCII
art text instead of an image, and there's no automatic polling — once you tell the
model you've scanned and approved it, it calls `nordnet_login_poll` itself to finish
the login.

A session obtained this way is kept in memory only — applied to the running server
immediately, but never written to `.env` or anywhere else on disk. If the server
process restarts for any reason, the token is gone and you'll need to log in again;
writing it back to `.env` would avoid that, but at a security cost (a bearer token
sitting in a plaintext file) that isn't worth it unless this turns out to be too
disruptive in practice — that's the tradeoff to revisit if so.
If the App view itself gets re-mounted (e.g. reopening a conversation you'd already
authenticated in), a QR is never shown for a session that's still good — this is
checked twice, both live against Nordnet (not just "is a token string present," since a
present-but-dead token shouldn't count): once server-side, before `nordnet_auth` would
otherwise generate a new order, and once again in the view itself before it ever
displays whatever content it was handed, since that content could be a stale replay of
a past result rather than a fresh call. Either way you just get a quiet "Already logged
in" instead of a pointless fresh QR.

Once logged in, the server also keeps the session alive on its own for as long as it
keeps running, the same way a browser tab left open on nordnet.se would — so you
generally won't need to re-authenticate just from being idle. This only helps while the
server process is actually running; stop it for longer than Nordnet's idle window and
you'll need to log in again next time it starts.

If you'd rather not use QR login, you can still provide a session token manually — see
[Manual token setup](#manual-token-setup).

## Features

- Read-only access to Nordnet portfolio/account data
- 16 MCP tools across accounts, instruments, reference data, and login
- QR-code login — no manual token extraction needed
- Supports Nordnet Sweden, Norway, Denmark, and Finland hosts
- Environment-variable based setup
- Packaged as a Python CLI entrypoint: `nordnet-mcp`

## Quickstart

### 1. Install uv

If you do not already have `uv`:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Set your market

Create a `.env` file in the working directory used to launch the server (typically the repo root when using `uv run --directory ...`):

```bash
cat > .env <<'EOF'
NORDNET_HOST=public.nordnet.se
# NORDNET_CLIENT_ID=NEXT
EOF
```

`NORDNET_CLIENT_ID` is optional. Nordnet session IDs appear to be tied to the
client that created them. If Nordnet responds with `NEXT_INVALID_SESSION`, add
`NORDNET_CLIENT_ID=NEXT` and restart the server.

Supported hosts:
- `public.nordnet.se`
- `public.nordnet.no`
- `public.nordnet.dk`
- `public.nordnet.fi`

`NORDNET_SESSION_TOKEN` doesn't need to be set up front — the QR login flow (see
Status above) gets you a session without it, kept in memory for the life of the server
process. If you'd rather not rely on that, set `NORDNET_SESSION_TOKEN` here yourself;
see [Manual token setup](#manual-token-setup).

### 3. Run the server locally

From a local clone:

```bash
git clone https://github.com/hpasic/nordnet-mcp.git
cd nordnet-mcp
uv run nordnet-mcp
```

If your `.env` is not in the repo root, export the variables in your shell before starting the server.

## MCP client setup

### Claude Code

```json
{
  "mcpServers": {
    "nordnet": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/absolute/path/to/nordnet-mcp",
        "nordnet-mcp"
      ]
    }
  }
}
```

### Claude Desktop / generic stdio MCP clients

```json
{
  "mcpServers": {
    "nordnet": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/absolute/path/to/nordnet-mcp",
        "nordnet-mcp"
      ],
      "env": {
        "NORDNET_SESSION_TOKEN": "your_token_here",
        "NORDNET_HOST": "public.nordnet.se"
      }
    }
  }
}
```

### Claude Desktop / generic stdio MCP clients (Docker)

```json
{
  "mcpServers": {
    "nordnet": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-e", "NORDNET_SESSION_TOKEN=your_token_here",
        "-e", "NORDNET_HOST=public.nordnet.se",
        "ghcr.io/hpasic/nordnet-mcp:latest"
      ]
    }
  }
}
```

Notes:
- Replace `/absolute/path/to/nordnet-mcp` with your local clone path.
- Supplying credentials through the MCP client's `env` block is often the easiest option.
- If Nordnet responds with `NEXT_INVALID_SESSION`, add `"NORDNET_CLIENT_ID": "NEXT"` to
  the `env` block (or `-e NORDNET_CLIENT_ID=NEXT` for Docker) — see the note above.
- When the session expires, either call `nordnet_auth` again or refresh the token
  manually and restart the server.

## Manual token setup

If you'd rather not rely on the QR flow, you can provide a session token yourself:

1. Log into `nordnet.se` (or your local Nordnet domain) in your browser.
2. Open DevTools.
3. Go to Application/Storage → Cookies.
4. Select the Nordnet domain.
5. Find the cookie named `NNX_SESSION_ID`.
6. Copy its value and set it as `NORDNET_SESSION_TOKEN` in `.env` (or the MCP client's
   `env` block).
7. It will usually look like a UUID-style value such as `7f3a91c2-5648-4dbe-8a17-29c4e6b1f053`.

## Available tools

### Accounts
- `list_accounts` — list all accounts with types and IDs
- `get_account_info` — balances and buying power
- `get_positions` — holdings with P&L
- `get_trades` — executed trades (0-7 days back)
- `get_ledgers` — currency balances
- `get_orders` — active orders
- `get_daily_transactions` — today's transactions

### Instruments
- `get_instrument` — instrument details (batch supported)
- `lookup_instrument` — find by ISIN or market ID
- `search_stocks` — free-text stock search
- `check_suitability` — check whether an instrument is tradeable

### Reference
- `get_countries` — country list
- `get_instrument_types` — instrument types
- `get_search_attributes` — search filter attributes

### Login
- `nordnet_auth` — start (or restart) QR-code login; renders as an MCP App image on
  supporting hosts, or ASCII art otherwise
- `nordnet_login_poll` — app-only, polls a pending login and finishes it once approved

## Development

```bash
uv sync --group dev
uv run pytest -q
uv build
```

## Security notes

- Do not commit `.env`.
- Do not paste your session token into issues or logs.
- The QR login flow never exposes the session token to the model or the MCP App view —
  it's applied straight to the running server's in-memory client and never written to
  disk. If you set `NORDNET_SESSION_TOKEN` manually instead, treat that `.env` file like
  it holds a password, because it does.
- Tokens are short-lived; if you get a 401/session-expired error, call `nordnet_auth`
  (or fetch a fresh token manually) rather than treating it as a hard failure.
- This server is read-only by design.

## Limitations

- Sessions are short-lived (a couple of hours) if idle, regardless of how they're
  obtained. The background keep-alive (see Status) only helps while the server process
  is actually running.
- A QR-obtained session is memory-only and does not survive a server restart, so a
  restart means logging in again. Use manual token setup instead if this is too
  disruptive in practice.
- QR login requires the Nordnet mobile app; if you can't use that, provide a manually
  extracted browser session token instead.
- The QR login and keep-alive mechanism relies on unofficial Nordnet behavior and may
  change without notice — same caveat as the rest of the API this project wraps.

## License

MIT
