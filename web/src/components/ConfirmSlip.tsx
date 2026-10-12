import { useEffect, useState } from "react";
import { AGENTS } from "../agents";
import type { PendingAction } from "../types";

interface Props {
  action: PendingAction;
  onConfirm: () => void;
  onDecline: () => void;
}

/** A request slip: nothing changes at the bank until the customer confirms it here. */
export function ConfirmSlip({ action, onConfirm, onDecline }: Props) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (action.state !== "pending") return;
    const t = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, [action.state]);

  const remaining = Math.max(0, Math.round(action.expiresAt - now));
  const expired = action.state === "pending" && remaining === 0;
  const state = expired ? "expired" : action.state;

  return (
    <section className="slip" data-state={state} aria-labelledby={`slip-${action.actionId}`}>
      <header className="slip-head">
        <h3 id={`slip-${action.actionId}`}>{action.title}</h3>
        <span className="slip-from">{AGENTS[action.agent]?.name ?? "Assistant"}</span>
      </header>
      <p className="slip-summary">{action.summary}</p>

      {state === "pending" && (
        <>
          {action.stepUp && <p className="slip-note">You'll be asked to verify it's you before this is sent.</p>}
          <div className="slip-actions">
            <button type="button" className="btn btn-primary" onClick={onConfirm}>
              Confirm request
            </button>
            <button type="button" className="btn btn-quiet" onClick={onDecline}>
              Cancel
            </button>
            <span className="slip-timer" aria-live="off">
              Expires in {Math.floor(remaining / 60)}:{String(remaining % 60).padStart(2, "0")}
            </span>
          </div>
        </>
      )}
      {state === "confirming" && <p className="slip-status" role="status">Sending to the bank…</p>}
      {state === "done" && <p className="slip-status" role="status">{action.result}</p>}
      {state === "declined" && <p className="slip-status" role="status">Cancelled. Nothing was changed.</p>}
      {state === "failed" && <p className="slip-status slip-error" role="alert">{action.result}</p>}
      {state === "expired" && <p className="slip-status">This request expired. Ask again to start over.</p>}
    </section>
  );
}
