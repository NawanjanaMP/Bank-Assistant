import { money } from "../agents";
import type { Account, Balance, ToolStep, Transaction } from "../types";

/** Structured results from the bank's systems, rendered alongside the assistant's words. */
export function ResultCards({ steps }: { steps: ToolStep[] }) {
  const ok = steps.filter((s) => s.status === "ok" && s.data);
  const accounts = (ok.find((s) => s.tool === "list_accounts")?.data as Account[] | undefined) ?? [];
  const balances = ok.filter((s) => s.tool === "get_balance").map((s) => s.data as Balance);
  const txs = ok.find((s) => s.tool === "get_recent_transactions")?.data as Transaction[] | undefined;
  const detail = ok.find((s) => s.tool === "get_transaction_details")?.data as Transaction | undefined;

  return (
    <>
      {balances.length > 0 && (
        <div className="balances" role="list">
          {balances.map((b) => {
            const acc = accounts.find((a) => a.account_id === b.account_id);
            return (
              <div className="balance" role="listitem" key={b.account_id}>
                <div className="balance-name">{acc?.nickname ?? acc?.type?.replace("_", " ") ?? b.account_id}</div>
                <div className="balance-num">{acc?.account_number}</div>
                <div className="balance-amount">{money(b.available, b.currency)}</div>
                <div className="balance-sub">
                  available{Number(b.ledger) !== Number(b.available) && ` · ${money(b.ledger, b.currency)} ledger`}
                </div>
              </div>
            );
          })}
        </div>
      )}
      {txs && txs.length > 0 && (
        <table className="ledger">
          <caption className="sr-only">Recent transactions</caption>
          <thead>
            <tr>
              <th scope="col">Date</th>
              <th scope="col">Description</th>
              <th scope="col" className="num">Amount</th>
            </tr>
          </thead>
          <tbody>
            {txs.map((t) => (
              <tr key={t.transaction_id}>
                <td className="ledger-date">
                  {new Date(t.booked_at).toLocaleDateString("en-GB", { day: "numeric", month: "short" })}
                </td>
                <td>
                  <span className="ledger-desc">{t.description}</span>
                  {t.category && <span className="ledger-cat">{t.category}</span>}
                </td>
                <td className={`num ${Number(t.amount) < 0 ? "debit" : "credit"}`}>
                  {Number(t.amount) > 0 && "+"}
                  {money(t.amount, "")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {detail && (
        <dl className="txdetail">
          <div><dt>Reference</dt><dd>{detail.transaction_id}</dd></div>
          <div><dt>Date</dt><dd>{new Date(detail.booked_at).toLocaleString("en-GB")}</dd></div>
          <div><dt>Amount</dt><dd className={Number(detail.amount) < 0 ? "debit" : "credit"}>{money(detail.amount, detail.currency)}</dd></div>
          <div><dt>Channel</dt><dd>{detail.channel ?? "-"}</dd></div>
          <div className="wide"><dt>Description</dt><dd>{detail.description}</dd></div>
        </dl>
      )}
    </>
  );
}
