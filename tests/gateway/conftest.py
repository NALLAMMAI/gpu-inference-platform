"""Fixtures that run the fake vLLM server and the real gateway code locally.

Both run as real uvicorn servers on free localhost ports in background
threads, so tests exercise actual HTTP between gateway and "vLLM", the
same way the gateway talks to the learning-vllm Service in the cluster.
"""

import importlib.util
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

REPO = Path(__file__).resolve().parents[2]
GATEWAY_FILE = REPO / "helm" / "gateway" / "files" / "main.py"
sys.path.insert(0, str(REPO / "dev"))

import fake_vllm  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Server:
    def __init__(self, app):
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        config = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def __enter__(self):
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("server did not start")
            time.sleep(0.01)
        return self

    def __exit__(self, *exc):
        self._server.should_exit = True
        self._thread.join(timeout=5)


def _load_gateway():
    spec = importlib.util.spec_from_file_location("gateway_main", GATEWAY_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def fake_vllm_server():
    with _Server(fake_vllm.app) as server:
        yield server


@pytest.fixture
def fast_vllm(monkeypatch):
    """Keep fake latencies small so the suite stays fast."""
    monkeypatch.setattr(fake_vllm, "TTFT_S", 0.01)
    monkeypatch.setattr(fake_vllm, "TPOT_S", 0.001)
    return fake_vllm


@pytest.fixture
def gateway_module(fake_vllm_server, monkeypatch):
    module = _load_gateway()
    monkeypatch.setattr(module, "VLLM_URL", fake_vllm_server.url)
    return module


@pytest.fixture
def gateway(gateway_module, fast_vllm):
    """A running gateway that forwards to the fake vLLM server."""
    with _Server(gateway_module.app) as server, httpx.Client(base_url=server.url, timeout=30) as client:
        yield client


@pytest.fixture
def unused_url():
    return f"http://127.0.0.1:{_free_port()}"
