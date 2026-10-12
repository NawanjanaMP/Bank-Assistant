"""Mock core banking HTTP service (stands in for the bank integration layer in local/dev/CI)."""

from __future__ import annotations

import os
import secrets

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from bankai.core_banking.client import CoreBankingError, InMemoryCoreBank
from bankai.core_banking.models import Address, ChequeBookRequest, KycUpdate, StatementRequest

bank = InMemoryCoreBank()
app = FastAPI(title="Mock Core Banking", version="1.0.0", docs_url=None, redoc_url=None)
_API_KEY = os.environ.get("BANKAI_CORE_BANKING_API_KEY", "dev-core-banking-key")


def _auth(x_api_key: str = Header(default="")) -> None:
    if not secrets.compare_digest(x_api_key, _API_KEY):
        raise HTTPException(401, "invalid api key")


def _idem(idempotency_key: str = Header(default="")) -> str:
    if len(idempotency_key) < 8:
        raise HTTPException(400, "Idempotency-Key header required")
    return idempotency_key


@app.exception_handler(CoreBankingError)
async def _cb_error(_: Request, exc: CoreBankingError) -> JSONResponse:
    return JSONResponse({"detail": str(exc), "code": exc.code}, status_code=exc.status)


@app.get("/healthz")
async def health() -> dict:
    return {"status": "ok"}


deps = [Depends(_auth)]


@app.get("/customers/{cid}/accounts", dependencies=deps)
async def accounts(cid: str):
    return await bank.list_accounts(cid)


@app.get("/customers/{cid}/accounts/{aid}/balance", dependencies=deps)
async def balance(cid: str, aid: str):
    return await bank.get_balance(cid, aid)


@app.get("/customers/{cid}/accounts/{aid}/transactions", dependencies=deps)
async def transactions(cid: str, aid: str, limit: int = 10):
    return await bank.get_transactions(cid, aid, limit)


@app.get("/customers/{cid}/transactions/{tid}", dependencies=deps)
async def transaction(cid: str, tid: str):
    return await bank.get_transaction(cid, tid)


@app.post("/customers/{cid}/statements", dependencies=deps)
async def statement(cid: str, body: StatementRequest, key: str = Depends(_idem)):
    return await bank.request_statement(cid, body, key)


@app.get("/customers/{cid}/profile", dependencies=deps)
async def profile(cid: str):
    return await bank.get_profile(cid)


@app.put("/customers/{cid}/address", dependencies=deps)
async def address(cid: str, body: Address, key: str = Depends(_idem)):
    return await bank.change_address(cid, body, key)


@app.post("/customers/{cid}/chequebooks", dependencies=deps)
async def chequebook(cid: str, body: ChequeBookRequest, key: str = Depends(_idem)):
    return await bank.request_cheque_book(cid, body, key)


@app.get("/customers/{cid}/kyc", dependencies=deps)
async def kyc(cid: str):
    return await bank.get_kyc_status(cid)


@app.patch("/customers/{cid}/kyc", dependencies=deps)
async def kyc_update(cid: str, body: KycUpdate, key: str = Depends(_idem)):
    return await bank.update_kyc(cid, body, key)


def run() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "9200")))  # noqa: S104  # nosec B104 - containers listen on all interfaces


if __name__ == "__main__":
    run()
