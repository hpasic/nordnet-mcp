# src/nordnet_mcp/__init__.py
from mcp.server.fastmcp import FastMCP

from nordnet_mcp import accounts, auth, instruments, reference
from nordnet_mcp.client import NordnetClient
from nordnet_mcp.config import load_config


def create_app() -> FastMCP:
    """Create and configure the Nordnet MCP server."""
    config = load_config()
    client = NordnetClient(
        session_token=config.session_token,
        host=config.host,
        client_id=config.client_id,
    )

    app = FastMCP("Nordnet", lifespan=auth.lifespan)

    # Configure modules with the HTTP client
    accounts.configure(client)
    instruments.configure(client)
    reference.configure(client)
    auth.configure(client, host=config.host)

    # Register tools from each module
    app = accounts.register_tools(app)
    app = instruments.register_tools(app)
    app = reference.register_tools(app)
    app = auth.register_tools(app)
    app = auth.register_resources(app)

    return app


def main():
    from dotenv import load_dotenv
    load_dotenv()
    app = create_app()
    app.run()
