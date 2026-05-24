# nordnet-mcp

Read-only MCP server for Nordnet portfolio data.

It exposes Nordnet accounts, positions, trades, balances, and instrument lookup/search to MCP-compatible clients such as Claude Code, Claude Desktop, and Cursor.

Important:
- This project is unofficial and is not affiliated with, endorsed by, or supported by Nordnet.
- It uses your existing Nordnet browser session token.
- The token is short-lived and typically expires after a couple of hours.
- Never commit your token or share your `.env` file.

## Status

This project is intended for technical users who are comfortable extracting a browser session token manually.

Today, the biggest setup friction is authentication, not the MCP server itself.

## Features

- Read-only access to Nordnet portfolio/account data
- 14 MCP tools across accounts, instruments, and reference data
- Supports Nordnet Sweden, Norway, Denmark, and Finland hosts
- Environment-variable based setup
- Packaged as a Python CLI entrypoint: `nordnet-mcp`

## Quickstart

### 1. Install uv

If you do not already have `uv`:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Get your Nordnet session token

1. Log into `nordnet.se` (or your local Nordnet domain) in your browser.
2. Open DevTools.
3. Go to Application/Storage → Cookies.
4. Select the Nordnet domain.
5. Find the cookie named `NNX_SESSION_ID`.
6. Copy its value and use that as `NORDNET_SESSION_TOKEN`.
7. It will usually look like a UUID-style value such as `7f3a91c2-5648-4dbe-8a17-29c4e6b1f053`.

### 3. Configure credentials

Create a `.env` file in the working directory used to launch the server (typically the repo root when using `uv run --directory ...`):

```bash
cat > .env <<'EOF'
NORDNET_SESSION_TOKEN=your_token_here
NORDNET_HOST=public.nordnet.se
EOF
```

Supported hosts:
- `public.nordnet.se`
- `public.nordnet.no`
- `public.nordnet.dk`
- `public.nordnet.fi`

### 4. Run the server locally

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
- When the token expires, update the token and restart the MCP server.

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

## Development

```bash
uv sync --group dev
uv run pytest -q
uv build
```

## Security notes

- Do not commit `.env`.
- Do not paste your session token into issues or logs.
- Tokens are short-lived; if you get a 401/session-expired error, fetch a fresh token from the browser.
- This server is read-only by design.

## Limitations

- Authentication depends on a manually extracted Nordnet browser session token.
- Tokens expire frequently.
- The API is unofficial and may change without notice.

## License

MIT
