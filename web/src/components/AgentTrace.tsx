import { useState } from "react";
import { AGENTS, RUNNING_LABEL, TOOL_LABEL } from "../agents";
import type { AgentId, ToolStep } from "../types";

/**
 * The routing thread: shows which specialist the assistant handed the question to and
 * every bank lookup it made, in plain language. Open while working, folds away after.
 */
export function AgentTrace({ routed, steps, streaming }: { routed: AgentId[]; steps: ToolStep[]; streaming: boolean }) {
  const [open, setOpen] = useState(false);
  if (!routed.length && !steps.length) return null;
  const expanded = streaming || open;
  const agents = routed.length ? routed : Array.from(new Set(steps.map((s) => s.agent)));
  const total = steps.reduce((sum, s) => sum + (s.latencyMs ?? 0), 0);

  return (
    <div className={`trace ${expanded ? "is-open" : ""}`}>
      <button
        type="button"
        className="trace-toggle"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={expanded}
        disabled={streaming}
      >
        <span className="trace-route">
          <span className="trace-node" data-agent="coordinator" />
          {agents.map((a) => (
            <span key={a} className="trace-hop" data-agent={a}>
              <span className="trace-link" />
              <span className="trace-node" data-agent={a} />
              {AGENTS[a].name}
            </span>
          ))}
        </span>
        {!streaming && (
          <span className="trace-meta">
            {steps.length} lookup{steps.length === 1 ? "" : "s"}
            {total > 0 && `, ${total < 1000 ? `${total} ms` : `${(total / 1000).toFixed(1)} s`}`}
          </span>
        )}
      </button>
      {expanded && (
        <ol className="trace-steps">
          {steps.map((s) => (
            <li key={s.callId} data-agent={s.agent} data-status={s.status}>
              <span className="trace-dot" aria-hidden />
              <span className="trace-label">
                {s.status === "running"
                  ? `${RUNNING_LABEL[s.tool] ?? TOOL_LABEL[s.tool] ?? s.tool}…`
                  : s.status === "denied"
                    ? `Not permitted: ${s.tool.replace(/_/g, " ")}`
                    : s.status === "error"
                      ? `Couldn't complete: ${(TOOL_LABEL[s.tool] ?? s.tool).toLowerCase()}`
                      : TOOL_LABEL[s.tool] ?? s.tool}
              </span>
              {s.latencyMs !== undefined && <span className="trace-ms">{s.latencyMs} ms</span>}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
