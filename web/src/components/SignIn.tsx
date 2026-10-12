import { AUTH_MODE, signIn } from "../auth";
import { BANK_NAME, Logo } from "./Logo";

export function SignIn() {
  return (
    <main className="signin">
      <div className="signin-card">
        <Logo />
        <h1>Banking help, any time</h1>
        <p>
          Check balances, find a payment, request a statement or update your details. Sign in with your {BANK_NAME}{" "}
          online banking login to continue.
        </p>
        {AUTH_MODE === "oidc" ? (
          <button type="button" className="btn btn-primary btn-wide" onClick={() => signIn()}>
            Sign in securely
          </button>
        ) : (
          <div className="signin-dev">
            <p className="signin-devnote">Development sign-in. Choose a demo customer:</p>
            <button type="button" className="btn btn-primary btn-wide" onClick={() => signIn("C-1001")}>
              Continue as Nimal Perera
            </button>
            <button type="button" className="btn btn-quiet btn-wide" onClick={() => signIn("C-1002")}>
              Continue as Ayesha Fernando
            </button>
          </div>
        )}
        <p className="signin-foot">We'll never ask for your PIN or one-time code in chat.</p>
        <p className="signin-copyright">&copy; {new Date().getFullYear()} Team Inferno</p>
      </div>
    </main>
  );
}
