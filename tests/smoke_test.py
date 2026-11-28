"""Simple smoke test against a port-forwarded OpenAI-compatible inference endpoint.

The model id comes from VLLM_MODEL, or from /v1/models when VLLM_MODEL is unset,
so the test keeps working when helm/vllm/values.yaml switches models.
"""

import json
import os
import urllib.request

base = os.environ.get("VLLM_BASE_URL", "http://localhost:8000").rstrip("/")
model = os.environ.get("VLLM_MODEL")
if not model:
    with urllib.request.urlopen(base + "/v1/models", timeout=30) as response:
        model = json.load(response)["data"][0]["id"]
body = json.dumps(
    {
        "model": model,
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
print(f"Inference smoke test passed ({model})")
