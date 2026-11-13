"""Simple smoke test against a port-forwarded OpenAI-compatible inference endpoint."""

import json
import os
import urllib.request

base = os.environ.get("VLLM_BASE_URL", "http://localhost:8000").rstrip("/")
body = json.dumps(
    {
        "model": os.environ.get("VLLM_MODEL", "Qwen/Qwen3-0.6B"),
        "messages": [{"role": "user", "content": "Say hello."}],
        "max_tokens": 32,
    }
).encode()
request = urllib.request.Request(
    base + "/v1/chat/completions",
    data=body,
    headers={"Content-Type": "application/json"},
)
with urllib.request.urlopen(request, timeout=120) as response:
    result = json.load(response)
assert result.get("choices"), result
print("Inference smoke test passed")
