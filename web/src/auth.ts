/**
 * Sign-in with the bank's identity provider (OIDC Authorization Code + PKCE).
 * Tokens stay in memory (sessionStorage fallback for reloads) - never localStorage.
 * VITE_AUTH_MODE=dev skips the IdP for local development against an API in dev auth mode.
 */
import { User, UserManager, WebStorageStateStore } from "oidc-client-ts";

const env = import.meta.env;
export const AUTH_MODE: "oidc" | "dev" = env.VITE_AUTH_MODE === "dev" ? "dev" : "oidc";

const manager =
  AUTH_MODE === "oidc"
    ? new UserManager({
        authority: env.VITE_OIDC_ISSUER ?? "http://localhost:8080/realms/bank",
        client_id: env.VITE_OIDC_CLIENT_ID ?? "bank-assistant-web",
        redirect_uri: `${window.location.origin}/`,
        post_logout_redirect_uri: `${window.location.origin}/`,
        response_type: "code",
        scope:
          "openid profile banking:accounts.read banking:transactions.read banking:statements.request " +
          "banking:profile.write banking:chequebook.request banking:kyc.write",
        userStore: new WebStorageStateStore({ store: window.sessionStorage }),
        automaticSilentRenew: true,
      })
    : null;

export interface Session {
  token: string;
  name: string;
}

const DEV_KEY = "dev-customer";

export async function currentSession(): Promise<Session | null> {
  if (AUTH_MODE === "dev") {
    const cid = sessionStorage.getItem(DEV_KEY);
    return cid ? { token: `dev:${cid}`, name: cid === "C-1001" ? "Nimal" : "Ayesha" } : null;
  }
  const params = new URLSearchParams(window.location.search);
  if (params.has("code") && params.has("state")) {
    const user = await manager!.signinRedirectCallback();
    const stepUpReturn = sessionStorage.getItem("step-up-return");
    window.history.replaceState({}, "", stepUpReturn ?? "/");
    sessionStorage.removeItem("step-up-return");
    return fromUser(user);
  }
  const user = await manager!.getUser();
  return user && !user.expired ? fromUser(user) : null;
}

function fromUser(user: User): Session {
  return { token: user.access_token, name: (user.profile.given_name as string) ?? user.profile.name ?? "there" };
}

export async function signIn(devCustomer = "C-1001"): Promise<void> {
  if (AUTH_MODE === "dev") {
    sessionStorage.setItem(DEV_KEY, devCustomer);
    window.location.reload();
    return;
  }
  await manager!.signinRedirect();
}

/** Re-authenticate with MFA before a sensitive change (OIDC step-up: max_age=0 + acr_values). */
export async function stepUp(): Promise<void> {
  if (AUTH_MODE === "dev") return;
  sessionStorage.setItem("step-up-return", window.location.pathname + window.location.hash);
  await manager!.signinRedirect({ max_age: 0, acr_values: "aal2", prompt: "login" });
}

export async function signOut(): Promise<void> {
  if (AUTH_MODE === "dev") {
    sessionStorage.removeItem(DEV_KEY);
    window.location.reload();
    return;
  }
  await manager!.signoutRedirect();
}
