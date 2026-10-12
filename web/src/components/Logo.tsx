export const BANK_NAME = import.meta.env.VITE_BANK_NAME ?? "Northwind Bank";

export function Logo() {
  return (
    <span className="logo">
      <svg viewBox="0 0 32 32" width="30" height="30" aria-hidden>
        <rect width="32" height="32" rx="9" fill="#0d4a55" />
        <path d="M8 22V10l8 7 8-7v12" fill="none" stroke="#f2b632" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <span className="logo-text">{BANK_NAME}</span>
    </span>
  );
}
