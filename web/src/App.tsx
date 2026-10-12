import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "./api";
import { currentSession, signOut, stepUp, type Session } from "./auth";
import { Composer } from "./components/Composer";
import { Logo } from "./components/Logo";
import { Message } from "./components/Message";
import { Sidebar } from "./components/Sidebar";
import { SignIn } from "./components/SignIn";
import type { PendingAction, SessionSummary } from "./types";
import { useConversation } from "./useConversation";

const SUGGESTIONS = [
  "What's my balance?",
  "Show my recent transactions",
  "Send me last month's statement",
  "I need a new cheque book",
  "I've moved and need to update my address",
  "Is my KYC review up to date?",
];

function greeting(name: string) {
  const h = new Date().getHours();
  const part = h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
  return `${part}, ${name}`;
}

export default function App() {
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  useEffect(() => {
    currentSession().then(setSession).catch(() => setSession(null));
  }, []);
  if (session === undefined) return <div className="boot" aria-busy="true" />;
  if (!session) return <SignIn />;
  return <Workspace session={session} />;
}

function Workspace({ session }: { session: Session }) {
  const { sessionId, turns, busy, send, load, setAction } = useConversation(session.token);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [drawer, setDrawer] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);

  const refresh = useCallback(() => {
    api.listSessions(session.token).then(setSessions).catch(() => undefined);
  }, [session.token]);

  useEffect(refresh, [refresh]);

  useEffect(() => {
    const el = scroller.current;
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 240) el.scrollTo({ top: el.scrollHeight });
  }, [turns]);

  const handleSend = async (text: string) => {
    await send(text);
    refresh();
  };

  const select = async (id: string | null) => {
    setDrawer(false);
    await load(id).catch(() => load(null));
  };

  const remove = async (id: string) => {
    await api.deleteSession(session.token, id).catch(() => undefined);
    if (id === sessionId) await load(null);
    refresh();
  };

  const confirm = async (a: PendingAction) => {
    if (!sessionId) return;
    setAction(a.actionId, { state: "confirming" });
    try {
      const r = await api.confirm(session.token, sessionId, a.actionId);
      const ref = r.receipt?.request_id;
      setAction(a.actionId, {
        state: r.ok ? "done" : "failed",
        result: r.ok ? `Submitted to the bank${ref ? `. Your reference is ${ref}.` : "."}` : r.message,
      });
    } catch (err) {
      if (err instanceof ApiError && err.code === "step_up_required") {
        setAction(a.actionId, { state: "pending" });
        await stepUp();
        return;
      }
      setAction(a.actionId, {
        state: err instanceof ApiError && err.status === 404 ? "expired" : "failed",
        result: err instanceof ApiError ? err.message : "Couldn't reach the bank. Try again.",
      });
    }
  };

  const decline = async (a: PendingAction) => {
    if (!sessionId) return;
    try {
      await api.decline(session.token, sessionId, a.actionId);
      setAction(a.actionId, { state: "declined" });
    } catch {
      setAction(a.actionId, { state: "expired" });
    }
  };

  return (
    <div className="shell">
      <Sidebar
        sessions={sessions}
        activeId={sessionId}
        name={session.name}
        open={drawer}
        onSelect={select}
        onDelete={remove}
        onSignOut={signOut}
        onClose={() => setDrawer(false)}
      />
      <main className="main">
        <header className="topbar">
          <button type="button" className="menu" onClick={() => setDrawer(true)} aria-label="Open conversations">
            <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden>
              <path d="M4 7h16M4 12h16M4 17h10" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
          </button>
          <Logo />
        </header>

        <div className="scroller" ref={scroller}>
          {turns.length === 0 ? (
            <section className="welcome">
              <h1>{greeting(session.name)}</h1>
              <p className="welcome-lead">What can I help you with today?</p>
              <ul className="suggestions">
                {SUGGESTIONS.map((s) => (
                  <li key={s}>
                    <button type="button" onClick={() => handleSend(s)} disabled={busy}>
                      {s}
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          ) : (
            <div className="thread" aria-live="polite">
              {turns.map((t) => (
                <Message key={t.id} turn={t} onConfirm={confirm} onDecline={decline} />
              ))}
            </div>
          )}
        </div>

        <Composer busy={busy} onSend={handleSend} />
      </main>
    </div>
  );
}
