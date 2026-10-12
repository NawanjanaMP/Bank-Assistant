"""Service MCP server: change of address, cheque book requests, KYC updates."""

from __future__ import annotations

from mcp.server.mcpserver import Context, MCPServer
from mcp_types import ToolAnnotations

from bankai.core_banking.client import CoreBanking
from bankai.core_banking.models import Address, ChequeBookRequest, KycUpdate
from bankai.mcp_servers.common import Guard
from bankai.shared.config import Settings

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False)


def build(core: CoreBanking, settings: Settings) -> MCPServer:
    guard = Guard("service", settings)
    mcp = MCPServer(
        "bank-service",
        instructions="Customer service requests. Every change requires explicit customer confirmation.",
        version="1.0.0",
    )

    @mcp.tool(annotations=READ_ONLY)
    async def get_profile(ctx: Context) -> dict:
        """Get the customer's current registered contact details and address."""

        async def _do(user, _):
            return await core.get_profile(user.customer_id)

        return await guard.run(ctx, "get_profile", {}, _do)

    @mcp.tool(annotations=WRITE)
    async def change_address(
        line1: str, city: str, postal_code: str, ctx: Context, line2: str | None = None, country: str = "LK"
    ) -> dict:
        """Change the customer's registered address. Requires confirmation and recent MFA.

        Args:
            line1: House number and street.
            city: City or town.
            postal_code: Postal code.
            line2: Optional apartment / area.
            country: ISO-3166 alpha-2 country code, default "LK".
        """
        args = {"line1": line1, "line2": line2, "city": city, "postal_code": postal_code, "country": country}

        async def _do(user, idem):
            return await core.change_address(user.customer_id, Address(**args), idem)

        return await guard.run(ctx, "change_address", args, _do)

    @mcp.tool(annotations=WRITE)
    async def request_cheque_book(account_id: str, ctx: Context, leaves: int = 25, delivery: str = "branch") -> dict:
        """Order a cheque book for a current account. Requires confirmation.

        Args:
            account_id: Current account id as returned by list_accounts.
            leaves: Number of leaves (10-100, default 25).
            delivery: "branch" or "post".
        """
        args = {"account_id": account_id, "leaves": leaves, "delivery": delivery}

        async def _do(user, idem):
            req = ChequeBookRequest(account_id=account_id, leaves=leaves, delivery=delivery)
            return await core.request_cheque_book(user.customer_id, req, idem)

        return await guard.run(ctx, "request_cheque_book", args, _do)

    @mcp.tool(annotations=READ_ONLY)
    async def get_kyc_status(ctx: Context) -> dict:
        """Get KYC (know-your-customer) review status and any missing information."""

        async def _do(user, _):
            return await core.get_kyc_status(user.customer_id)

        return await guard.run(ctx, "get_kyc_status", {}, _do)

    @mcp.tool(annotations=WRITE)
    async def update_kyc(
        ctx: Context,
        occupation: str | None = None,
        employer: str | None = None,
        annual_income_band: str | None = None,
        source_of_funds: str | None = None,
    ) -> dict:
        """Submit updated KYC information for review. Requires confirmation and recent MFA.

        Args:
            occupation: Current occupation.
            employer: Current employer name.
            annual_income_band: One of "<1M", "1M-3M", "3M-6M", "6M-12M", ">12M" (LKR).
            source_of_funds: Main source of funds (e.g. salary, business income).
        """
        args = {"occupation": occupation, "employer": employer, "annual_income_band": annual_income_band,
                "source_of_funds": source_of_funds}

        async def _do(user, idem):
            return await core.update_kyc(user.customer_id, KycUpdate(**args), idem)

        return await guard.run(ctx, "update_kyc", args, _do)

    return mcp
