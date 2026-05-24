import json

_client = None


def configure(client):
    global _client
    _client = client


def register_tools(app):

    @app.tool()
    async def list_accounts() -> str:
        """List all Nordnet accounts with IDs and types."""
        data = await _client.get("/accounts")
        return json.dumps(data, indent=2)

    @app.tool()
    async def get_account_info(account_id: int) -> str:
        """Get account balances, buying power, and interest rates."""
        data = await _client.get(f"/accounts/{account_id}/info")
        if isinstance(data, list) and data:
            data = data[0]
        # Nordnet has used both *_acc keys and plain keys here. The current
        # public API returns plain keys (`account_sum`, `trading_power`, ...),
        # while older docs/tests used accumulated `*_acc` variants.
        account_sum = data.get("account_sum") or data.get("account_sum_acc") or {}
        buying_power = data.get("trading_power") or data.get("buying_power_acc") or {}
        collateral = data.get("collateral") or data.get("collateral_acc") or {}
        info = {
            "total_value": account_sum.get("value"),
            "currency": account_sum.get("currency"),
            "buying_power": buying_power.get("value"),
            "collateral": collateral.get("value"),
            "interest": data.get("interest", {}).get("value"),
        }
        info = {k: v for k, v in info.items() if v is not None}
        return json.dumps(info, indent=2)

    @app.tool()
    async def get_positions(account_id: int) -> str:
        """Get all holdings/positions for an account.

        Returns instrument name, quantity, average price, current value,
        P&L amount and percentage for each position.
        """
        data = await _client.get(f"/accounts/{account_id}/positions")
        positions = []
        for p in data:
            pos = {
                "instrument_id": p.get("instrument", {}).get("instrument_id"),
                "name": p.get("instrument", {}).get("name"),
                "quantity": p.get("qty"),
                "avg_price": p.get("acq_price_acc", {}).get("value"),
                "current_value": p.get("market_value_acc", {}).get("value"),
                "currency": p.get("market_value_acc", {}).get("currency"),
                "pnl": p.get("morning_pnl_acc", {}).get("value"),
                "pnl_pct": p.get("morning_pnl_pct"),
            }
            pos = {k: v for k, v in pos.items() if v is not None}
            positions.append(pos)
        return json.dumps(positions, indent=2)

    @app.tool()
    async def get_trades(account_id: int, days: int = 0) -> str:
        """Get executed trades for an account (0-7 days back).

        Args:
            account_id: The account ID
            days: Number of days back (0 = today only, max 7)
        """
        params = {}
        if days:
            params["days"] = days
        data = await _client.get(f"/accounts/{account_id}/trades", params=params)
        return json.dumps(data, indent=2)

    @app.tool()
    async def get_ledgers(account_id: int) -> str:
        """Get currency ledger balances for an account."""
        data = await _client.get(f"/accounts/{account_id}/ledgers")
        return json.dumps(data, indent=2)

    @app.tool()
    async def get_orders(account_id: int) -> str:
        """Get active orders for an account."""
        data = await _client.get(f"/accounts/{account_id}/orders")
        return json.dumps(data, indent=2)

    @app.tool()
    async def get_daily_transactions(account_id: int) -> str:
        """Get today's transactions for an account."""
        data = await _client.get(
            f"/accounts/{account_id}/returns/transactions/today"
        )
        return json.dumps(data, indent=2)

    return app
