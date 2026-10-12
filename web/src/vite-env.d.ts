/// <reference types="vite/client" />
interface ImportMetaEnv {
  readonly VITE_AUTH_MODE?: "oidc" | "dev";
  readonly VITE_OIDC_ISSUER?: string;
  readonly VITE_OIDC_CLIENT_ID?: string;
  readonly VITE_API_BASE?: string;
  readonly VITE_BANK_NAME?: string;
}
