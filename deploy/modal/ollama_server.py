"""Self-hosted LLM on Modal's free-tier GPU, wired into bankai's ``self_hosted`` provider.

Runs Ollama on a T4 GPU behind an authenticated FastAPI proxy (Modal's own web endpoints are
public by default; bankai's OpenAICompatClient already sends ``Authorization: Bearer <token>``,
so the proxy just checks that token before forwarding to Ollama's local OpenAI-compatible API).

Model weights are cached in a Modal Volume so only the first deploy pays the download cost.

Setup (one-time):
    modal setup                                                  # browser login, run by the user
    modal secret create bankai-llm-auth BANKAI_LLM_TOKEN=<random-token>

Deploy:
    modal deploy deploy/modal/ollama_server.py

Point bankai at it:
    BANKAI_SELF_HOSTED_BASE_URL=<printed modal.run URL>/v1
    BANKAI_SELF_HOSTED_API_KEY=<the same random token>
    BANKAI_SELF_HOSTED_MODEL=qwen2.5:14b-instruct
    bankai-eval --provider self_hosted
"""

import os
import subprocess
import time

import modal

MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:14b-instruct")
OLLAMA_PORT = 11434

app = modal.App("bankai-local-llm")
volume = modal.Volume.from_name("bankai-ollama-models", create_if_missing=True)
auth_secret = modal.Secret.from_name("bankai-llm-auth")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("curl", "zstd")
    .run_commands("curl -fsSL https://ollama.com/install.sh | sh")
    .pip_install("fastapi[standard]", "httpx")
)


def _wait_for_ollama() -> None:
    import httpx

    for _ in range(60):
        try:
            httpx.get(f"http://127.0.0.1:{OLLAMA_PORT}/api/tags", timeout=2)
            return
        except httpx.HTTPError:
            time.sleep(1)
    raise RuntimeError("ollama server did not come up in time")


@app.function(
    image=image,
    gpu="T4",
    volumes={"/root/.ollama": volume},
    secrets=[auth_secret],
    timeout=600,
    scaledown_window=1800,  # keep warm for 30min after the last request (demo-friendly; ~$0.30/hr while idle-warm)
)
@modal.concurrent(max_inputs=4)
@modal.asgi_app()
def serve():
    import httpx
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import StreamingResponse

    subprocess.Popen(["ollama", "serve"])
    _wait_for_ollama()
    subprocess.run(["ollama", "pull", MODEL], check=True)  # near-instant once cached in the volume
    volume.commit()

    token = os.environ["BANKAI_LLM_TOKEN"]
    web_app = FastAPI()
    upstream = f"http://127.0.0.1:{OLLAMA_PORT}"

    @web_app.api_route("/v1/{path:path}", methods=["GET", "POST"])
    async def proxy(path: str, request: Request):
        if request.headers.get("authorization") != f"Bearer {token}":
            raise HTTPException(status_code=401, detail="unauthorized")
        body = await request.body()
        client = httpx.AsyncClient(timeout=180)
        upstream_req = client.build_request(
            request.method, f"{upstream}/v1/{path}", content=body,
            headers={"content-type": "application/json"}, params=request.query_params,
        )
        resp = await client.send(upstream_req, stream=True)

        async def relay():
            try:
                async for chunk in resp.aiter_bytes():
                    yield chunk
            finally:
                await resp.aclose()
                await client.aclose()

        return StreamingResponse(relay(), status_code=resp.status_code,
                                 media_type=resp.headers.get("content-type", "application/json"))

    return web_app
