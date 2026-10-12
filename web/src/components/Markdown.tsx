import { Fragment, type ReactNode } from "react";

/** Minimal, safe renderer for assistant text: paragraphs, "- " lists and **bold**. No raw HTML. */
function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith("**") && part.endsWith("**") ? <strong key={i}>{part.slice(2, -2)}</strong> : <Fragment key={i}>{part}</Fragment>,
  );
}

export function Markdown({ text }: { text: string }) {
  const blocks = text.trim().split(/\n{2,}/);
  return (
    <>
      {blocks.map((block, i) => {
        const lines = block.split("\n");
        const items = lines.filter((l) => /^\s*[-*•]\s+/.test(l));
        if (items.length && items.length === lines.filter((l) => l.trim()).length) {
          return (
            <ul key={i}>
              {items.map((l, j) => (
                <li key={j}>{inline(l.replace(/^\s*[-*•]\s+/, ""))}</li>
              ))}
            </ul>
          );
        }
        if (items.length) {
          const head = lines.filter((l) => !/^\s*[-*•]\s+/.test(l)).join(" ");
          return (
            <Fragment key={i}>
              <p>{inline(head)}</p>
              <ul>
                {items.map((l, j) => (
                  <li key={j}>{inline(l.replace(/^\s*[-*•]\s+/, ""))}</li>
                ))}
              </ul>
            </Fragment>
          );
        }
        return <p key={i}>{lines.map((l, j) => <Fragment key={j}>{j > 0 && <br />}{inline(l)}</Fragment>)}</p>;
      })}
    </>
  );
}
