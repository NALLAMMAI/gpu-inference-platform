import json
import os
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

VLLM_URL = os.environ.get("VLLM_BASE_URL", "http://learning-vllm:8000").rstrip("/")
CONNECT_TIMEOUT_S = float(os.environ.get("GATEWAY_CONNECT_TIMEOUT_S", "5"))
# Maximum wait for the next bytes from vLLM: the whole response when not
# streaming, or the gap between two chunks when streaming.
READ_TIMEOUT_S = float(os.environ.get("GATEWAY_READ_TIMEOUT_S", "120"))

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    """One shared connection pool to vLLM, instead of a new client per request."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
        )
    return _client


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    if _client is not None:
        await _client.aclose()


app = FastAPI(title="GPU Inference Learning Gateway", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "fastapi-gateway"}


def wants_stream(body: bytes) -> bool:
    try:
        return json.loads(body).get("stream") is True
    except (ValueError, AttributeError):
        return False


async def forward(request: Request, path: str):
    body = await request.body()
    headers = {"content-type": request.headers.get("content-type", "application/json")}
    # Internal learning cluster only. No public ingress or authentication in this demo.
    client = get_client()
    upstream_request = client.build_request(
        request.method, f"{VLLM_URL}{path}", content=body or None, headers=headers
    )
    start = time.perf_counter()
    try:
        upstream = await client.send(upstream_request, stream=True)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail="vLLM is not available yet") from exc

    media_type = upstream.headers.get("content-type", "application/json")

    if wants_stream(body) and upstream.status_code == 200:
        # Pass each chunk (one or more SSE events) to the client as soon as vLLM sends it.
        return StreamingResponse(
            relay(upstream, start),
            status_code=upstream.status_code,
            media_type=media_type,
            headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
        )

    # Non-streaming requests and upstream errors: read the whole body, as before.
    try:
        content = await upstream.aread()
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail="vLLM is not available yet") from exc
    finally:
        await upstream.aclose()
    latency_ms = (time.perf_counter() - start) * 1000
    print(f"vLLM request latency: {latency_ms:.2f} ms", flush=True)
    return Response(content=content, status_code=upstream.status_code, media_type=media_type)


async def relay(upstream: httpx.Response, start: float):
    first_chunk_ms = None
    try:
        async for chunk in upstream.aiter_raw():
            if first_chunk_ms is None:
                first_chunk_ms = (time.perf_counter() - start) * 1000
            yield chunk
    except httpx.RequestError as exc:
        # vLLM went away mid-stream; the client sees a truncated stream with no [DONE].
        print(f"vLLM stream interrupted: {exc!r}", flush=True)
    finally:
        # Also runs when the client disconnects, which closes the upstream request so
        # vLLM can abort generation instead of finishing an answer nobody reads.
        await upstream.aclose()
        total_ms = (time.perf_counter() - start) * 1000
        first = f"{first_chunk_ms:.2f} ms" if first_chunk_ms is not None else "none"
        print(f"vLLM stream: first chunk {first}, total {total_ms:.2f} ms", flush=True)


@app.get("/v1/models")
async def models(request: Request):
    return await forward(request, "/v1/models")


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    return await forward(request, "/v1/chat/completions")
