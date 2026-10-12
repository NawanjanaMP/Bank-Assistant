"""Public API (behind the edge layer / API gateway)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from bankai.orchestrator.observability import metrics
from bankai.orchestrator.security.oidc import AuthError, Principal, bearer_token
from bankai.orchestrator.security.ratelimit import RateLimited
from bankai.orchestrator.service import ActionError
from bankai.orchestrator.session.store import SessionNotFound

router = APIRouter(prefix="/v1")


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class SessionIn(BaseModel):
    title: str | None = Field(default=None, max_length=80)


def principal(request: Request) -> Principal:
    try:
        return request.app.state.verifier.verify(bearer_token(request))
    except AuthError as exc:
        status = 403 if exc.code == "forbidden" else 401
        raise HTTPException(status, detail={"code": exc.code, "message": str(exc)},
                            headers={"WWW-Authenticate": "Bearer"} if status == 401 else None) from exc


async def owned_session(sid: str, request: Request, p: Principal = Depends(principal)) -> str:
    try:
        await request.app.state.store.get_meta(sid, p.subject)
    except SessionNotFound as exc:
        raise HTTPException(404, detail={"code": "session_not_found", "message": "Conversation not found"}) from exc
    return sid


@router.get("/me")
async def me(p: Principal = Depends(principal)) -> dict[str, Any]:
    return {"subject": p.subject, "name": p.name, "scopes": sorted(p.scopes)}


@router.post("/sessions", status_code=201)
async def create_session(body: SessionIn, request: Request, p: Principal = Depends(principal)) -> dict[str, str]:
    sid = await request.app.state.store.create(p.subject, body.title or "New conversation")
    return {"id": sid}


@router.get("/sessions")
async def list_sessions(request: Request, p: Principal = Depends(principal)) -> list[dict[str, str]]:
    return await request.app.state.store.list_sessions(p.subject)


@router.get("/sessions/{sid}")
async def get_session(request: Request, sid: str = Depends(owned_session),
                      p: Principal = Depends(principal)) -> dict[str, Any]:
    st = request.app.state
    meta = await st.store.get_meta(sid, p.subject)
    mapping = await st.store.vault_all(sid)
    messages = []
    for m in await st.store.history(sid):
        if isinstance(m["content"], str) and not m["content"].startswith("[customer "):
            messages.append({"role": m["role"], "text": await st.redactor.restore_text(sid, m["content"], mapping),
                             "agents": m.get("agents", []), "ts": m.get("ts")})
    meta.pop("owner", None)
    return {"id": sid, **meta, "messages": messages, "usage": await st.cost.session_usage(sid)}


@router.delete("/sessions/{sid}", status_code=204)
async def delete_session(request: Request, sid: str = Depends(owned_session),
                         p: Principal = Depends(principal)) -> Response:
    await request.app.state.store.delete(sid, p.subject)
    return Response(status_code=204)


def _sse(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, default=str, ensure_ascii=False)}\n\n".encode()


@router.post("/sessions/{sid}/messages")
async def send_message(body: MessageIn, request: Request, sid: str = Depends(owned_session),
                       p: Principal = Depends(principal)) -> StreamingResponse:
    st = request.app.state
    try:
        await st.ratelimiter.hit(p.subject)
    except RateLimited as exc:
        metrics.GUARDRAIL_EVENTS.labels("rate_limited").inc()
        raise HTTPException(429, detail={"code": "rate_limited", "message": "Too many messages, slow down."},
                            headers={"Retry-After": str(exc.retry_after)}) from exc

    queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue()

    async def emit(event: str, data: dict[str, Any]) -> None:
        await queue.put((event, data))

    async def run() -> None:
        try:
            await st.turns.handle_turn(p, sid, body.text, emit)
        finally:
            await queue.put(None)

    # The turn runs to completion even if the client disconnects, so history stays consistent.
    task = asyncio.create_task(run())
    st.background_tasks.add(task)
    task.add_done_callback(st.background_tasks.discard)

    async def stream() -> AsyncIterator[bytes]:
        metrics.ACTIVE_STREAMS.inc()
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield b": keep-alive\n\n"
                    continue
                if item is None:
                    break
                yield _sse(*item)
        finally:
            metrics.ACTIVE_STREAMS.dec()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


async def _action(request: Request, fn: str, sid: str, action_id: str, p: Principal) -> dict[str, Any]:
    try:
        return await getattr(request.app.state.turns, fn)(p, sid, action_id)
    except ActionError as exc:
        challenge = 'Bearer error="insufficient_user_authentication", acr_values="aal2", max_age="0"'
        headers = {"WWW-Authenticate": challenge} if exc.code == "step_up_required" else None
        raise HTTPException(exc.status, detail={"code": exc.code, "message": str(exc)}, headers=headers) from exc


@router.post("/sessions/{sid}/actions/{action_id}/confirm")
async def confirm_action(action_id: str, request: Request, sid: str = Depends(owned_session),
                         p: Principal = Depends(principal)) -> dict[str, Any]:
    return await _action(request, "confirm", sid, action_id, p)


@router.post("/sessions/{sid}/actions/{action_id}/decline")
async def decline_action(action_id: str, request: Request, sid: str = Depends(owned_session),
                         p: Principal = Depends(principal)) -> dict[str, Any]:
    return await _action(request, "decline", sid, action_id, p)
