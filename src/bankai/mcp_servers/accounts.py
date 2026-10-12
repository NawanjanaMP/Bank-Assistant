"""Accounts MCP server: balance enquiry."""

from __future__ import annotations

from mcp.server.mcpserver import Context, MCPServer
from mcp_types import ToolAnnotations

from bankai.core_banking.client import CoreBanking
from bankai.mcp_servers.common import Guard, mask_account_number
from bankai.shared.config import Settings

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)


def build(core: CoreBanking, settings: Settings) -> MCPServer:
    guard = Guard("accounts", settings)
    mcp = MCPServer(
        "bank-accounts",
        instructions="Read-only access to the authenticated customer's accounts and balances.",
        version="1.0.0",
    )

    @mcp.tool(annotations=READ_ONLY)
    async def list_accounts(ctx: Context) -> list[dict]:
        """List the customer's accounts (id, masked number, type, nickname, currency, status).
        Call this first when the customer has not said which account they mean."""

        async def _do(user, _):
            accounts = await core.list_accounts(user.customer_id)
            return [
                a.model_dump(mode="json") | {"account_number": mask_account_number(a.account_number)}
                for a in accounts
            ]

        return await guard.run(ctx, "list_accounts", {}, _do)

    @mcp.tool(annotations=READ_ONLY)
    async def get_balance(account_id: str, ctx: Context) -> dict:
        """Get available and ledger balance for one of the customer's accounts.

        Args:
            account_id: Account id as returned by list_accounts (e.g. "A-1").
        """

        async def _do(user, _):
            return await core.get_balance(user.customer_id, account_id)

        return await guard.run(ctx, "get_balance", {"account_id": account_id}, _do)

    return mcp
