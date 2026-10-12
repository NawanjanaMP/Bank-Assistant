"""Signed user-context token: how the orchestrator tells an MCP server *who* is asking.

The LLM never supplies identity. The orchestrator mints a short-lived, audience-scoped
JWT carrying the authenticated customer id, granted scopes and a step-up flag, and
attaches it to every MCP tool call in request ``_meta``. Each MCP server verifies it
independently (defence in depth) before touching core banking.

HS256 with a shared secret keeps the reference deployment simple; for production,
switch to an asymmetric key (ES256) held in KMS/HSM so MCP servers only hold the
public key. The interface below does not change.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

import jwt

META_KEY = "bank.ai/user-context"
ALGORITHM = "HS256"


class ContextTokenError(Exception):
    pass


@dataclass(frozen=True)
class UserContext:
    subject: str
    customer_id: str
    scopes: frozenset[str] = field(default_factory=frozenset)
    stepped_up: bool = False
    session_id: str | None = None
    request_id: str | None = None
    # Set only when the customer explicitly confirmed a write action: "<tool>:<sha256 of args>".
    approved_action: str | None = None

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


def mint(ctx: UserContext, *, key: str, issuer: str, audience: str, ttl: int) -> str:
    now = int(time.time())
    claims = {
        "iss": issuer,
        "aud": audience,
        "sub": ctx.subject,
        "cid": ctx.customer_id,
        "scp": sorted(ctx.scopes),
        "stp": ctx.stepped_up,
        "sid": ctx.session_id,
        "rid": ctx.request_id,
        "act": ctx.approved_action,
        "iat": now,
        "nbf": now - 5,
        "exp": now + ttl,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(claims, key, algorithm=ALGORITHM)


def verify(token: str, *, key: str, issuer: str, audience: str) -> UserContext:
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=[ALGORITHM],
            audience=audience,
            issuer=issuer,
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
            leeway=5,
        )
    except jwt.PyJWTError as exc:
        raise ContextTokenError(f"invalid user context token: {exc}") from exc
    if not claims.get("cid"):
        raise ContextTokenError("user context token has no customer id")
    return UserContext(
        subject=claims["sub"],
        customer_id=claims["cid"],
        scopes=frozenset(claims.get("scp", [])),
        stepped_up=bool(claims.get("stp")),
        session_id=claims.get("sid"),
        request_id=claims.get("rid"),
        approved_action=claims.get("act"),
    )


def action_fingerprint(tool: str, arguments: dict) -> str:
    """Stable fingerprint binding an approval to one tool and its exact arguments."""
    import hashlib
    import json

    # Values are compared as strings and None/absent are equivalent, so "25" == 25 and a
    # default filled in by the server matches the same default filled in by the orchestrator.
    normalised = {k: str(v) for k, v in arguments.items() if v is not None}
    canonical = json.dumps(normalised, sort_keys=True, separators=(",", ":"))
    return f"{tool}:{hashlib.sha256(canonical.encode()).hexdigest()}"
