"""Authentication against the bank's identity provider (OIDC / OAuth 2.0 access tokens).

The web app performs Authorization Code + PKCE against the IdP; the API only ever sees
bearer access tokens and validates them locally against the IdP's JWKS (cached, rotated
automatically). Checks: signature, issuer, audience, expiry, authorised party (azp),
customer-id claim. ``auth_time`` / ``acr`` drive step-up decisions for sensitive actions.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import jwt
from fastapi import Request
from jwt import PyJWKClient

from bankai.shared.config import AuthMode, Settings


class AuthError(Exception):
    def __init__(self, message: str, code: str = "unauthenticated") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Principal:
    subject: str
    customer_id: str
    scopes: frozenset[str] = field(default_factory=frozenset)
    auth_time: int | None = None
    acr: str | None = None
    name: str | None = None

    def is_stepped_up(self, settings: Settings) -> bool:
        recent = self.auth_time is not None and time.time() - self.auth_time <= settings.step_up_max_age_seconds
        strong = self.acr in settings.step_up_acr_values if self.acr else False
        return recent and (strong or settings.auth_mode is AuthMode.DEV)


ALL_BANKING_SCOPES = frozenset({
    "banking:accounts.read", "banking:transactions.read", "banking:statements.request",
    "banking:profile.write", "banking:chequebook.request", "banking:kyc.write",
})


class TokenVerifier:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self._jwks = PyJWKClient(settings.jwks_url, cache_keys=True, lifespan=600) \
            if settings.auth_mode is AuthMode.OIDC else None

    def verify(self, token: str) -> Principal:
        if self.s.auth_mode is AuthMode.DEV:
            return self._dev_principal(token)
        if self._jwks is None:
            raise AuthError("OIDC is not configured")
        try:
            key = self._jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token, key, algorithms=["RS256", "ES256", "PS256"], audience=self.s.oidc_audience,
                issuer=self.s.oidc_issuer, options={"require": ["exp", "iat", "sub", "iss"]}, leeway=10,
            )
        except jwt.PyJWTError as exc:
            raise AuthError(f"invalid token: {type(exc).__name__}") from exc
        if self.s.oidc_allowed_azp and claims.get("azp") not in self.s.oidc_allowed_azp:
            raise AuthError("token issued to an unexpected client")
        customer_id = claims.get(self.s.customer_id_claim)
        if not customer_id:
            raise AuthError("token has no customer id", code="forbidden")
        scopes = set(str(claims.get("scope", "")).split())
        return Principal(
            subject=claims["sub"], customer_id=str(customer_id), scopes=frozenset(scopes),
            auth_time=claims.get("auth_time"), acr=claims.get("acr"),
            name=claims.get("given_name") or claims.get("name"),
        )

    def _dev_principal(self, token: str) -> Principal:
        # "dev:<customer_id>" - only reachable when AUTH_MODE=dev, which config forbids in staging/prod.
        if not token.startswith("dev:"):
            raise AuthError("dev mode expects 'Bearer dev:<customer_id>'")
        cid = token.removeprefix("dev:") or "C-1001"
        return Principal(subject=f"dev-{cid}", customer_id=cid, scopes=ALL_BANKING_SCOPES,
                         auth_time=int(time.time()), acr="aal2", name="Nimal" if cid == "C-1001" else "Ayesha")


def bearer_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise AuthError("missing bearer token")
    return token.strip()
