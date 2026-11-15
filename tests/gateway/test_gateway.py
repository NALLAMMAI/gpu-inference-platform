"""Behaviour tests for the FastAPI gateway in helm/gateway/files/main.py.

These pin down what the gateway does today, so later changes (streaming,
metrics, auth) can be made safely. Known gaps are marked xfail(strict=True):
when a change fixes one, the test starts passing, pytest reports XPASS as a
failure, and the marker must be removed.
"""

import json
import time

import httpx
import pytest

MODEL = "Qwen/Qwen3-0.6B"


def chat_body(**overrides):
    body = {"model": MODEL, "messages": [{"role": "user", "content": "Say hello."}], "max_tokens": 8}
    body.update(overrides)
    return body


def test_health(gateway):
    r = gateway.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "service": "fastapi-gateway"}


def test_models_are_forwarded(gateway):
    r = gateway.get("/v1/models")
    assert r.status_code == 200
    assert [m["id"] for m in r.json()["data"]] == [MODEL]


def test_chat_completion_is_forwarded(gateway):
    r = gateway.post("/v1/chat/completions", json=chat_body(max_tokens=5))
    assert r.status_code == 200
    data = r.json()
    assert data["object"] == "chat.completion"
    assert data["choices"][0]["message"]["content"] == "token token token token token"
    assert data["usage"]["completion_tokens"] == 5


def test_upstream_error_status_is_passed_through(gateway):
    r = gateway.post("/v1/chat/completions", json=chat_body(model="no-such-model"))
    assert r.status_code == 404
    assert "does not exist" in r.json()["error"]["message"]


def test_vllm_unreachable_returns_503(gateway, gateway_module, unused_url, monkeypatch):
    monkeypatch.setattr(gateway_module, "VLLM_URL", unused_url)
    r = gateway.get("/v1/models")
    assert r.status_code == 503
    assert r.json()["detail"] == "vLLM is not available yet"


def test_streamed_response_content_arrives_intact(gateway):
    r = gateway.post("/v1/chat/completions", json=chat_body(stream=True, max_tokens=4))
    assert r.status_code == 200
    events = [line.removeprefix("data: ") for line in r.text.splitlines() if line.startswith("data: ")]
    assert events[-1] == "[DONE]"
    text = "".join(json.loads(e)["choices"][0]["delta"].get("content", "") for e in events[:-1])
    assert text == "token token token token"


def test_streaming_first_chunk_arrives_before_generation_finishes(gateway, fast_vllm):
    fast_vllm.TPOT_S = 0.05  # 20 tokens -> about 1 s of generation
    start = time.perf_counter()
    with gateway.stream("POST", "/v1/chat/completions", json=chat_body(stream=True, max_tokens=20)) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        chunks = r.iter_bytes()
        first_chunk = next(chunks)
        time_to_first_chunk = time.perf_counter() - start
        assert first_chunk
        for _ in chunks:
            pass
    total = time.perf_counter() - start
    assert time_to_first_chunk < total / 2


def test_streaming_upstream_error_is_passed_through(gateway):
    r = gateway.post("/v1/chat/completions", json=chat_body(stream=True, model="no-such-model"))
    assert r.status_code == 404
    assert "does not exist" in r.json()["error"]["message"]


def test_stream_false_is_not_streamed(gateway):
    r = gateway.post("/v1/chat/completions", json=chat_body(stream=False, max_tokens=3))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["choices"][0]["message"]["content"] == "token token token"


def test_connection_pool_is_shared_across_requests(gateway, gateway_module):
    gateway.get("/v1/models")
    first_client = gateway_module._client
    gateway.post("/v1/chat/completions", json=chat_body(max_tokens=2))
    gateway.post("/v1/chat/completions", json=chat_body(stream=True, max_tokens=2))
    assert first_client is not None
    assert gateway_module._client is first_client
    assert not first_client.is_closed


def test_client_disconnect_stops_upstream_generation(gateway, fast_vllm):
    fast_vllm.TPOT_S = 0.02  # 64 tokens -> about 1.3 s if generation is not stopped
    with gateway.stream("POST", "/v1/chat/completions", json=chat_body(stream=True, max_tokens=64)) as r:
        next(r.iter_bytes())
        assert fast_vllm._state["running"] == 1
    # Leaving the block closes the client connection mid-stream.
    deadline = time.monotonic() + 1.0
    while fast_vllm._state["running"] and time.monotonic() < deadline:
        time.sleep(0.02)
    assert fast_vllm._state["running"] == 0


@pytest.mark.xfail(strict=True, reason="Gateway exposes no Prometheus /metrics endpoint yet")
def test_gateway_exposes_prometheus_metrics(gateway):
    gateway.get("/v1/models")
    r = gateway.get("/metrics")
    assert r.status_code == 200
    assert "http_requests" in r.text or "gateway_requests" in r.text


@pytest.mark.xfail(strict=True, reason="Gateway forwards no request ID header yet")
def test_request_id_is_returned(gateway):
    r = gateway.get("/v1/models", headers={"X-Request-ID": "test-123"})
    assert r.headers.get("x-request-id") == "test-123"


def test_fake_vllm_metrics_look_like_vllm(fake_vllm_server):
    r = httpx.get(f"{fake_vllm_server.url}/metrics")
    assert r.status_code == 200
    for name in ("vllm:num_requests_running", "vllm:num_requests_waiting", "vllm:kv_cache_usage_perc"):
        assert name in r.text
