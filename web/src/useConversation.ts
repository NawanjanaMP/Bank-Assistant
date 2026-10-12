import { useCallback, useRef, useState } from "react";
import { ApiError, api, sendMessage } from "./api";
import type { AgentId, PendingAction, Turn } from "./types";

let seq = 0;
const nextId = () => `t${Date.now().toString(36)}${(seq++).toString(36)}`;

function emptyTurn(role: Turn["role"], text?: string): Turn {
  return { id: nextId(), role, text, segments: [], steps: [], routed: [], actions: [], notices: [], streaming: false };
}

export function useConversation(token: string) {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const patchLast = useCallback((fn: (t: Turn) => Turn) => {
    setTurns((prev) => (prev.length ? [...prev.slice(0, -1), fn(prev[prev.length - 1])] : prev));
  }, []);

  const load = useCallback(
    async (id: string | null) => {
      abortRef.current?.abort();
      setSessionId(id);
      if (!id) {
        setTurns([]);
        return;
      }
      const s = await api.getSession(token, id);
      setTurns(
        s.messages.map((m) => {
          const t = emptyTurn(m.role, m.role === "user" ? m.text : undefined);
          if (m.role === "assistant") t.segments = [{ agent: (m.agents?.[0] as AgentId) ?? "coordinator", text: m.text }];
          return t;
        }),
      );
    },
    [token],
  );

  const send = useCallback(
    async (text: string): Promise<string> => {
      let sid = sessionId;
      if (!sid) {
        sid = (await api.createSession(token)).id;
        setSessionId(sid);
      }
      const assistant = { ...emptyTurn("assistant"), streaming: true };
      setTurns((prev) => [...prev, emptyTurn("user", text), assistant]);
      setBusy(true);
      const ctrl = new AbortController();
      abortRef.current = ctrl;

      const onEvent = (event: string, d: Record<string, any>) => {
        patchLast((t) => {
          switch (event) {
            case "agent.routed":
              return { ...t, routed: [...t.routed, d.agent] };
            case "message.delta": {
              const segs = [...t.segments];
              const last = segs[segs.length - 1];
              if (last && last.agent === d.agent) segs[segs.length - 1] = { ...last, text: last.text + d.text };
              else segs.push({ agent: d.agent, text: d.text });
              return { ...t, segments: segs };
            }
            case "message.break": {
              // Interim "let me check" text before tool calls: keep it, start a new paragraph after.
              const segs = [...t.segments];
              const last = segs[segs.length - 1];
              if (last) segs[segs.length - 1] = { ...last, text: last.text.trimEnd() + "\n\n" };
              return { ...t, segments: segs };
            }
            case "tool.started":
              return { ...t, steps: [...t.steps, { callId: d.call_id, agent: d.agent, tool: d.tool, status: "running" }] };
            case "tool.completed":
              return {
                ...t,
                steps: t.steps.map((s) =>
                  s.callId === d.call_id ? { ...s, status: d.status, latencyMs: d.latency_ms, data: d.data } : s,
                ),
              };
            case "confirmation.required": {
              const action: PendingAction = {
                actionId: d.action_id, tool: d.tool, agent: d.agent, title: d.title, summary: d.summary,
                stepUp: d.step_up, expiresAt: d.expires_at, state: "pending",
              };
              return { ...t, actions: [...t.actions, action] };
            }
            case "guardrail":
              return d.type === "pii_redacted"
                ? { ...t, notices: [...t.notices, { kind: "pii_redacted", message: `${d.count} personal detail${d.count === 1 ? "" : "s"} hidden from the AI model` }] }
                : { ...t, notices: [...t.notices, { kind: d.type, message: d.message }] };
            case "error":
              return { ...t, notices: [...t.notices, { kind: "error", message: d.message }] };
            case "turn.completed":
              return { ...t, streaming: false, latencyMs: d.latency_ms };
            default:
              return t;
          }
        });
      };

      try {
        await sendMessage(token, sid, text, onEvent, ctrl.signal);
      } catch (err) {
        if (!(err instanceof DOMException && err.name === "AbortError")) {
          const message =
            err instanceof ApiError
              ? err.status === 429
                ? `You're sending messages quickly. Try again in ${err.retryAfter ?? 30} seconds.`
                : err.message
              : "Can't reach the assistant. Check your connection and try again.";
          patchLast((t) => ({ ...t, notices: [...t.notices, { kind: "error", message }] }));
        }
      } finally {
        patchLast((t) => ({ ...t, streaming: false }));
        setBusy(false);
      }
      return sid;
    },
    [patchLast, sessionId, token],
  );

  const setAction = useCallback((actionId: string, patch: Partial<PendingAction>) => {
    setTurns((prev) =>
      prev.map((t) => ({
        ...t,
        actions: t.actions.map((a) => (a.actionId === actionId ? { ...a, ...patch } : a)),
      })),
    );
  }, []);

  return { sessionId, turns, busy, send, load, setAction };
}
