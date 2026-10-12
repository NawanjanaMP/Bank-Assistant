export type AgentId = "coordinator" | "accounts" | "transactions" | "service";

export interface ToolStep {
  callId: string;
  agent: AgentId;
  tool: string;
  status: "running" | "ok" | "error" | "denied" | "awaiting_confirmation";
  latencyMs?: number;
  data?: unknown;
}

export interface Segment {
  agent: AgentId;
  text: string;
}

export interface PendingAction {
  actionId: string;
  tool: string;
  agent: AgentId;
  title: string;
  summary: string;
  stepUp: boolean;
  expiresAt: number;
  state: "pending" | "confirming" | "done" | "declined" | "failed" | "expired";
  result?: string;
}

export interface Turn {
  id: string;
  role: "user" | "assistant";
  text?: string; // user text, or restored history text
  segments: Segment[];
  steps: ToolStep[];
  routed: AgentId[];
  actions: PendingAction[];
  notices: { kind: "pii_redacted" | "prompt_injection" | "authorisation" | "error"; message: string }[];
  streaming: boolean;
  latencyMs?: number;
}

export interface SessionSummary {
  id: string;
  title: string;
  updated_at?: string;
}

export interface Account {
  account_id: string;
  account_number: string;
  type: string;
  nickname?: string | null;
  currency: string;
}

export interface Balance {
  account_id: string;
  currency: string;
  available: string;
  ledger: string;
  as_of: string;
}

export interface Transaction {
  transaction_id: string;
  booked_at: string;
  amount: string;
  currency: string;
  description: string;
  category?: string | null;
  channel?: string | null;
}
