import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";

const MAX = 2000;

export function Composer({ busy, onSend }: { busy: boolean; onSend: (text: string) => void }) {
  const [text, setText] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
  }, [text]);

  useEffect(() => {
    if (!busy) ref.current?.focus();
  }, [busy]);

  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    const value = text.trim();
    if (!value || busy) return;
    onSend(value);
    setText("");
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <form className="composer" onSubmit={submit}>
      <label htmlFor="composer-input" className="sr-only">
        Message the assistant
      </label>
      <div className="composer-box">
        <textarea
          id="composer-input"
          ref={ref}
          rows={1}
          value={text}
          maxLength={MAX}
          placeholder="Ask about your balance, a payment, or a service request"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
        />
        <button type="submit" className="send" disabled={busy || !text.trim()} aria-label="Send message">
          <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden>
            <path d="M4 12h14M12 5l7 7-7 7" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      </div>
      <p className="composer-hint">
        Never share your PIN, password or one-time code. The assistant can't move money; changes need your confirmation.
      </p>
    </form>
  );
}
