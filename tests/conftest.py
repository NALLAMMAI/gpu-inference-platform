"""Shared fixtures: the fake vLLM server and a helper that runs any ASGI app
as a real uvicorn server on a free localhost port in a background thread."""

import contextlib
import socket
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn

REPO = Path(__file__).resolve().parents[1]
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
def serve():
    """Start ASGI apps as real servers for one test: `server = serve(app)`."""
    with contextlib.ExitStack() as stack:
        yield lambda app: stack.enter_context(_Server(app))


@pytest.fixture
def unused_url():
    return f"http://127.0.0.1:{_free_port()}"
