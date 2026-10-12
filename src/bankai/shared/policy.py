"""Single source of truth for capabilities, scopes and the tool policy.

Both the orchestrator (Authorisation component) and every MCP server import this
module, so a tool can never be reachable on one side and unchecked on the other.
A tool missing from TOOL_POLICY is denied by default.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Scope(StrEnum):
    ACCOUNTS_READ = "banking:accounts.read"
    TRANSACTIONS_READ = "banking:transactions.read"
    STATEMENTS_REQUEST = "banking:statements.request"
    PROFILE_WRITE = "banking:profile.write"
    CHEQUEBOOK_REQUEST = "banking:chequebook.request"
    KYC_WRITE = "banking:kyc.write"


class AgentName(StrEnum):
    COORDINATOR = "coordinator"
    ACCOUNTS = "accounts"
    TRANSACTIONS = "transactions"
    SERVICE = "service"


@dataclass(frozen=True)
class ToolPolicy:
    agent: AgentName
    scope: Scope
    write: bool = False  # mutates state at the bank -> needs explicit user confirmation
    step_up: bool = False  # needs a recent strong authentication (MFA) at confirmation time
    summary: str = ""  # human-readable template shown on the confirmation card


TOOL_POLICY: dict[str, ToolPolicy] = {
    # Accounts MCP server
    "list_accounts": ToolPolicy(AgentName.ACCOUNTS, Scope.ACCOUNTS_READ),
    "get_balance": ToolPolicy(AgentName.ACCOUNTS, Scope.ACCOUNTS_READ),
    # Transactions MCP server
    "get_recent_transactions": ToolPolicy(AgentName.TRANSACTIONS, Scope.TRANSACTIONS_READ),
    "get_transaction_details": ToolPolicy(AgentName.TRANSACTIONS, Scope.TRANSACTIONS_READ),
    "request_statement": ToolPolicy(
        AgentName.TRANSACTIONS,
        Scope.STATEMENTS_REQUEST,
        write=True,
        summary="Send a statement for account {account_id} from {from_date} to {to_date} by {delivery}",
    ),
    # Service MCP server
    "get_profile": ToolPolicy(AgentName.SERVICE, Scope.PROFILE_WRITE),
    "change_address": ToolPolicy(
        AgentName.SERVICE,
        Scope.PROFILE_WRITE,
        write=True,
        step_up=True,
        summary="Change your registered address to: {line1}, {city} {postal_code}, {country}",
    ),
    "request_cheque_book": ToolPolicy(
        AgentName.SERVICE,
        Scope.CHEQUEBOOK_REQUEST,
        write=True,
        summary="Order a {leaves}-leaf cheque book for account {account_id}, delivered to {delivery}",
    ),
    "get_kyc_status": ToolPolicy(AgentName.SERVICE, Scope.KYC_WRITE),
    "update_kyc": ToolPolicy(
        AgentName.SERVICE,
        Scope.KYC_WRITE,
        write=True,
        step_up=True,
        summary="Update your KYC details: {field_summary}",
    ),
}

# Scope a customer needs before the coordinator will even delegate to an agent.
AGENT_REQUIRED_SCOPES: dict[AgentName, set[Scope]] = {
    AgentName.ACCOUNTS: {Scope.ACCOUNTS_READ},
    AgentName.TRANSACTIONS: {Scope.TRANSACTIONS_READ},
    AgentName.SERVICE: set(),  # each service tool is checked individually
}


def policy_for(tool_name: str) -> ToolPolicy | None:
    return TOOL_POLICY.get(tool_name)
