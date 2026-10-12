"""Specialist agent definitions: which MCP servers and tools each agent may use.

Allow-lists are explicit (least privilege). Read-only ``list_accounts`` is shared because
transactions and service requests need to resolve "my current account" etc.
"""

from bankai.orchestrator.agents.base import AgentSpec
from bankai.shared.policy import AgentName

ACCOUNTS = AgentSpec(
    name=AgentName.ACCOUNTS,
    title="Accounts Agent",
    description="Balances and the customer's list of accounts.",
    servers=("accounts",),
    tools=frozenset({"list_accounts", "get_balance"}),
)

TRANSACTIONS = AgentSpec(
    name=AgentName.TRANSACTIONS,
    title="Transaction Agent",
    description="Recent transactions, transaction details and statement requests.",
    servers=("accounts", "transactions"),
    tools=frozenset({"list_accounts", "get_recent_transactions", "get_transaction_details", "request_statement"}),
)

SERVICE = AgentSpec(
    name=AgentName.SERVICE,
    title="Service Agent",
    description="Change of address, cheque book requests, KYC status and updates.",
    servers=("accounts", "service"),
    tools=frozenset({"list_accounts", "get_profile", "change_address", "request_cheque_book",
                     "get_kyc_status", "update_kyc"}),
)

ALL = {spec.name: spec for spec in (ACCOUNTS, TRANSACTIONS, SERVICE)}
