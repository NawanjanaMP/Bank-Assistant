import type { SessionSummary } from "./types";

const BASE = import.meta.env.VITE_API_BASE ?? "";

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public retryAfter?: number) {
    super(message);
  }
}

async function request<T>(token: string, path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", ...(init.headers ?? {}) },
  });
  if (!res.ok) throw await toError(res);
  return (res.status === 204 ? undefined : await res.json()) as T;
}

async function toError(res: Response): Promise<ApiError> {
  let code = "http_error";
  let message = `Request failed (${res.status})`;
  try {
    const body = await res.json();
    code = body.detail?.code ?? code;
    message = body.detail?.message ?? message;
  } catch {
    /* non-JSON body */
  }
  return new ApiError(res.status, code, message, Number(res.headers.get("Retry-After")) || undefined);
}

export const api = {
  createSession: (t: string) => request<{ id: string }>(t, "/v1/sessions", { method: "POST", body: "{}" }),
  listSessions: (t: string) => request<SessionSummary[]>(t, "/v1/sessions"),
  getSession: (t: string, id: string) =>
    request<{ id: string; title: string; messages: { role: "user" | "assistant"; text: string; agents: string[] }[] }>(
      t,
      `/v1/sessions/${id}`,
    ),
  deleteSession: (t: string, id: string) => request<void>(t, `/v1/sessions/${id}`, { method: "DELETE" }),
  confirm: (t: string, sid: string, aid: string) =>
    request<{ ok: boolean; message: string; receipt?: { request_id?: string } }>(
      t,
      `/v1/sessions/${sid}/actions/${aid}/confirm`,
      { method: "POST" },
    ),
  decline: (t: string, sid: string, aid: string) =>
    request<{ ok: boolean; message: string }>(t, `/v1/sessions/${sid}/actions/${aid}/decline`, { method: "POST" }),
};

export type StreamHandler = (event: string, data: Record<string, unknown>) => void;

/** POST a message and consume the Server-Sent Events stream of the turn. */
export async function sendMessage(
  token: string,
  sid: string,
  text: string,
  onEvent: StreamHandler,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${BASE}/v1/sessions/${sid}/messages`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ text }),
    signal,
  });
  if (!res.ok || !res.body) throw await toError(res);
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const chunk = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      let event = "message";
      const data: string[] = [];
      for (const line of chunk.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) data.push(line.slice(6));
      }
      if (data.length) onEvent(event, JSON.parse(data.join("\n")));
    }
  }
}
