"""Fake vLLM server for local development and tests. No GPU or model needed.

Implements the subset of vLLM's OpenAI-compatible API that this repo uses:

    GET  /health
    GET  /v1/models
    POST /v1/chat/completions   (non-streaming and "stream": true SSE)
    GET  /metrics               (a few vllm:* Prometheus metrics)

Responses are deterministic: the reply is the word "token" repeated
min(max_tokens, FAKE_VLLM_MAX_TOKENS) times, with finish_reason "length". Timing is controlled with:

    FAKE_VLLM_TTFT_MS          delay before the first token (default 20)
    FAKE_VLLM_TPOT_MS          delay per output token (default 5)
    FAKE_VLLM_MAX_TOKENS       cap on generated tokens (default 64)
    FAKE_VLLM_MODEL            model id to report (default Qwen/Qwen3-0.6B)

Run it with:  make run-fake-vllm   (listens on 127.0.0.1:8001)
"""

import asyncio
import json
import os
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

MODEL = os.environ.get("FAKE_VLLM_MODEL", "Qwen/Qwen3-0.6B")
TTFT_S = float(os.environ.get("FAKE_VLLM_TTFT_MS", "20")) / 1000
TPOT_S = float(os.environ.get("FAKE_VLLM_TPOT_MS", "5")) / 1000
MAX_TOKENS_CAP = int(os.environ.get("FAKE_VLLM_MAX_TOKENS", "64"))

app = FastAPI(title="Fake vLLM (local development)")

_state = {"running": 0, "prompt_tokens": 0, "generation_tokens": 0, "requests": 0}


@app.get("/health")
async def health():
    return PlainTextResponse("")


@app.get("/v1/models")
async def models():
    return {
        "object": "list",
        "data": [
            {
                "id": MODEL,
                "object": "model",
                "created": 0,
                "owned_by": "fake-vllm",
                "max_model_len": 4096,
            }
        ],
    }


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"message": message, "type": "BadRequestError", "code": status}},
    )


def _prompt_tokens(messages: list) -> int:
    # Rough whitespace token count; good enough for usage numbers in tests.
    return sum(len(str(m.get("content", "")).split()) for m in messages)


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return _error(400, "Request body is not valid JSON")

    if body.get("model") != MODEL:
        return _error(404, f"The model `{body.get('model')}` does not exist.")
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return _error(400, "`messages` must be a non-empty list")

    n_out = max(1, min(int(body.get("max_tokens") or 16), MAX_TOKENS_CAP))
    n_in = _prompt_tokens(messages)
    req_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    _state["requests"] += 1
    _state["prompt_tokens"] += n_in

    if body.get("stream"):
        return StreamingResponse(
            _stream(req_id, created, n_in, n_out, body.get("stream_options") or {}),
            media_type="text/event-stream",
        )

    _state["running"] += 1
    try:
        await asyncio.sleep(TTFT_S + TPOT_S * n_out)
    finally:
        _state["running"] -= 1
    _state["generation_tokens"] += n_out
    return {
        "id": req_id,
        "object": "chat.completion",
        "created": created,
        "model": MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": " ".join(["token"] * n_out)},
                "finish_reason": "length",
            }
        ],
        "usage": {"prompt_tokens": n_in, "completion_tokens": n_out, "total_tokens": n_in + n_out},
    }


async def _stream(req_id: str, created: int, n_in: int, n_out: int, stream_options: dict):
    def chunk(delta: dict, finish_reason=None) -> str:
        payload = {
            "id": req_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": MODEL,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        return f"data: {json.dumps(payload)}\n\n"

    _state["running"] += 1
    try:
        await asyncio.sleep(TTFT_S)
        yield chunk({"role": "assistant", "content": ""})
        for i in range(n_out):
            if i:
                await asyncio.sleep(TPOT_S)
            yield chunk({"content": "token" if i == 0 else " token"})
            _state["generation_tokens"] += 1
        yield chunk({}, finish_reason="length")
        if stream_options.get("include_usage"):
            usage = {"prompt_tokens": n_in, "completion_tokens": n_out, "total_tokens": n_in + n_out}
            payload = {
                "id": req_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": MODEL,
                "choices": [],
                "usage": usage,
            }
            yield f"data: {json.dumps(payload)}\n\n"
        yield "data: [DONE]\n\n"
    finally:
        _state["running"] -= 1


@app.get("/metrics")
async def metrics():
    label = f'model_name="{MODEL}"'
    lines = [
        "# TYPE vllm:num_requests_running gauge",
        f"vllm:num_requests_running{{{label}}} {_state['running']}",
        "# TYPE vllm:num_requests_waiting gauge",
        f"vllm:num_requests_waiting{{{label}}} 0",
        "# TYPE vllm:kv_cache_usage_perc gauge",
        f"vllm:kv_cache_usage_perc{{{label}}} {min(0.05 * _state['running'], 1.0)}",
        "# TYPE vllm:prompt_tokens_total counter",
        f"vllm:prompt_tokens_total{{{label}}} {_state['prompt_tokens']}",
        "# TYPE vllm:generation_tokens_total counter",
        f"vllm:generation_tokens_total{{{label}}} {_state['generation_tokens']}",
    ]
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")
