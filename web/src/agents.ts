import type { AgentId } from "./types";

export const AGENTS: Record<AgentId, { name: string; short: string }> = {
  coordinator: { name: "Assistant", short: "Assistant" },
  accounts: { name: "Accounts", short: "Accounts" },
  transactions: { name: "Transactions", short: "Transactions" },
  service: { name: "Customer service", short: "Service" },
};

/** Plain-language description of what each bank lookup or request does. */
export const TOOL_LABEL: Record<string, string> = {
  list_accounts: "Found your accounts",
  get_balance: "Read balance",
  get_recent_transactions: "Read recent transactions",
  get_transaction_details: "Opened the transaction",
  request_statement: "Prepared a statement request",
  get_profile: "Read your registered details",
  change_address: "Prepared an address change",
  request_cheque_book: "Prepared a cheque book order",
  get_kyc_status: "Checked your KYC review",
  update_kyc: "Prepared a KYC update",
};

export const RUNNING_LABEL: Record<string, string> = {
  list_accounts: "Finding your accounts",
  get_balance: "Reading balance",
  get_recent_transactions: "Reading transactions",
  get_transaction_details: "Opening the transaction",
  get_profile: "Reading your details",
  get_kyc_status: "Checking your KYC review",
};

export const money = (v: string | number, currency = "LKR") => {
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n)
    ? `${currency} ${n.toLocaleString("en-LK", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
    : String(v);
};
