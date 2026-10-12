import { AGENTS } from "../agents";
import type { PendingAction, Turn } from "../types";
import { AgentTrace } from "./AgentTrace";
import { ConfirmSlip } from "./ConfirmSlip";
import { Markdown } from "./Markdown";
import { ResultCards } from "./ResultCards";

interface Props {
  turn: Turn;
  onConfirm: (a: PendingAction) => void;
  onDecline: (a: PendingAction) => void;
}

export function Message({ turn, onConfirm, onDecline }: Props) {
  if (turn.role === "user") {
    return (
      <div className="msg msg-user">
        <p>{turn.text}</p>
      </div>
    );
  }

  const thinking = turn.streaming && !turn.segments.length && !turn.steps.length;
  const multi = new Set(turn.segments.map((s) => s.agent)).size > 1;

  return (
    <article className="msg msg-assistant" aria-busy={turn.streaming}>
      <AgentTrace routed={turn.routed} steps={turn.steps} streaming={turn.streaming} />
      {thinking && (
        <div className="thinking" role="status">
          <span />
          <span />
          <span />
          <span className="sr-only">Working on it</span>
        </div>
      )}
      {turn.segments.map((seg, i) => {
        // Each specialist's cards sit directly under its own answer.
        const lastOfAgent = !turn.segments.slice(i + 1).some((s) => s.agent === seg.agent);
        return (
          <div className="segment-group" key={i}>
            <div className="segment" data-agent={seg.agent}>
              {multi && seg.agent !== "coordinator" && <div className="segment-from">{AGENTS[seg.agent].name}</div>}
              <div className="prose">
                <Markdown text={seg.text} />
                {turn.streaming && i === turn.segments.length - 1 && <span className="caret" aria-hidden />}
              </div>
            </div>
            {lastOfAgent && <ResultCards steps={turn.steps.filter((s) => s.agent === seg.agent)} />}
          </div>
        );
      })}
      {!turn.segments.length && <ResultCards steps={turn.steps} />}
      {turn.actions.map((a) => (
        <ConfirmSlip key={a.actionId} action={a} onConfirm={() => onConfirm(a)} onDecline={() => onDecline(a)} />
      ))}
      {turn.notices.map((n, i) => (
        <p key={i} className={`notice notice-${n.kind}`} role={n.kind === "error" ? "alert" : undefined}>
          {n.message}
        </p>
      ))}
    </article>
  );
}
