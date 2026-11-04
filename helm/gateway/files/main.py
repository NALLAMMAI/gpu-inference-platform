import os
import time
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

app = FastAPI(title="GPU Inference Learning Gateway")
VLLM_URL = os.environ.get("VLLM_BASE_URL", "http://learning-vllm:8000").rstrip("/")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "fastapi-gateway"}


async def forward(request: Request, path: str):
    try:
        body = await request.body()
        headers = {"content-type": request.headers.get("content-type", "application/json")}
        # Internal learning cluster only. No public ingress or authentication in this demo.
        async with httpx.AsyncClient(timeout=120.0) as client:
            start = time.perf_counter()
            upstream = await client.request(
                request.method,
                f"{VLLM_URL}{path}",
                content=body if body else None,
                headers=headers,
            )
            latency_ms = (time.perf_counter() - start) * 1000
            print(f"vLLM request latency: {latency_ms:.2f} ms", flush=True)
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/json"),
        )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail="vLLM is not available yet") from exc


@app.get("/v1/models")
async def models(request: Request):
    return await forward(request, "/v1/models")


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    return await forward(request, "/v1/chat/completions")
