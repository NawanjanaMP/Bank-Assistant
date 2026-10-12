import type { SessionSummary } from "../types";
import { Logo } from "./Logo";

interface Props {
  sessions: SessionSummary[];
  activeId: string | null;
  name: string;
  open: boolean;
  onSelect: (id: string | null) => void;
  onDelete: (id: string) => void;
  onSignOut: () => void;
  onClose: () => void;
}

function when(ts?: string) {
  if (!ts) return "";
  const d = new Date(Number(ts) * 1000);
  const today = new Date();
  return d.toDateString() === today.toDateString()
    ? d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

export function Sidebar({ sessions, activeId, name, open, onSelect, onDelete, onSignOut, onClose }: Props) {
  return (
    <>
      <div className={`scrim ${open ? "is-open" : ""}`} onClick={onClose} aria-hidden />
      <nav className={`sidebar ${open ? "is-open" : ""}`} aria-label="Conversations">
        <div className="sidebar-brand">
          <Logo />
        </div>
        <button type="button" className="btn btn-new" onClick={() => onSelect(null)}>
          <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden>
            <path d="M12 5v14M5 12h14" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
          </svg>
          New conversation
        </button>
        <h2 className="sidebar-heading">Recent</h2>
        {sessions.length === 0 ? (
          <p className="sidebar-empty">Your conversations from the last 30 minutes appear here.</p>
        ) : (
          <ul className="sessions">
            {sessions.map((s) => (
              <li key={s.id} className={s.id === activeId ? "is-active" : ""}>
                <button type="button" className="session" onClick={() => onSelect(s.id)} aria-current={s.id === activeId}>
                  <span className="session-title">{s.title}</span>
                  <span className="session-time">{when(s.updated_at)}</span>
                </button>
                <button type="button" className="session-delete" onClick={() => onDelete(s.id)} aria-label={`Delete conversation: ${s.title}`}>
                  <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden>
                    <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                  </svg>
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="sidebar-foot">
          <span className="avatar" aria-hidden>{name.slice(0, 1).toUpperCase()}</span>
          <span className="sidebar-user">{name}</span>
          <button type="button" className="btn btn-quiet btn-small" onClick={onSignOut}>
            Sign out
          </button>
        </div>
        <p className="sidebar-copyright">&copy; {new Date().getFullYear()} Team Inferno</p>
      </nav>
    </>
  );
}
