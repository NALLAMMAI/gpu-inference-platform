"""Fixtures that run the real gateway code locally, in front of the fake vLLM
server from tests/conftest.py.

Both run as real uvicorn servers on free localhost ports in background
threads, so tests exercise actual HTTP between gateway and "vLLM", the
same way the gateway talks to the learning-vllm Service in the cluster.
"""

import importlib.util
from pathlib import Path

import httpx
import pytest

GATEWAY_FILE = Path(__file__).resolve().parents[2] / "helm" / "gateway" / "files" / "main.py"


def _load_gateway():
    spec = importlib.util.spec_from_file_location("gateway_main", GATEWAY_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def gateway_module(fake_vllm_server, monkeypatch):
    module = _load_gateway()
    monkeypatch.setattr(module, "VLLM_URL", fake_vllm_server.url)
    return module


@pytest.fixture
def gateway(gateway_module, fast_vllm, serve):
    """A running gateway that forwards to the fake vLLM server."""
    server = serve(gateway_module.app)
    with httpx.Client(base_url=server.url, timeout=30) as client:
        yield client
