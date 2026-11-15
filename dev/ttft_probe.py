"""Measure streaming time to first token (TTFT) and total latency for an
OpenAI-compatible chat endpoint: the local gateway, the fake vLLM, or a
port-forwarded gateway or vLLM on the cluster.

    uv run python dev/ttft_probe.py --url http://127.0.0.1:8080 --requests 20
    make ttft-local            # gateway on :8080 -> fake vLLM on :8001

Requests are sent one at a time ("stream": true). TTFT is measured on the
client, from sending the request to receiving the first chunk with non-empty
content. Run it against the gateway and against vLLM directly to see how much
latency the gateway adds.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time

import httpx


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, round(p / 100 * (len(ordered) - 1))))
    return ordered[k]


def one_request(client: httpx.Client, url: str, model: str, max_tokens: int) -> tuple[float, float, int]:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "Explain Kubernetes in one paragraph."}],
        "max_tokens": max_tokens,
        "stream": True,
    }
    start = time.perf_counter()
    ttft = None
    chunks = 0
    with client.stream("POST", f"{url}/v1/chat/completions", json=body) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            choices = json.loads(line[6:]).get("choices") or []
            if choices and choices[0].get("delta", {}).get("content"):
                chunks += 1
                if ttft is None:
                    ttft = time.perf_counter() - start
    total = time.perf_counter() - start
    return (ttft if ttft is not None else total), total, chunks


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--url", default="http://127.0.0.1:8080", help="base URL, without /v1")
    parser.add_argument("--model", default="Qwen/Qwen3-0.6B")
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--json", action="store_true", help="print results as JSON")
    args = parser.parse_args()

    ttfts, totals, chunk_counts = [], [], []
    with httpx.Client(timeout=300) as client:
        one_request(client, args.url, args.model, 4)  # warm-up, not recorded
        for _ in range(args.requests):
            ttft, total, chunks = one_request(client, args.url, args.model, args.max_tokens)
            ttfts.append(ttft * 1000)
            totals.append(total * 1000)
            chunk_counts.append(chunks)

    result = {
        "url": args.url,
        "requests": args.requests,
        "max_tokens": args.max_tokens,
        "ttft_ms": {
            "mean": statistics.mean(ttfts),
            "p50": percentile(ttfts, 50),
            "p95": percentile(ttfts, 95),
        },
        "total_ms": {
            "mean": statistics.mean(totals),
            "p50": percentile(totals, 50),
            "p95": percentile(totals, 95),
        },
        "content_chunks_mean": statistics.mean(chunk_counts),
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        t, tot = result["ttft_ms"], result["total_ms"]
        print(f"{args.url}  requests={args.requests}  max_tokens={args.max_tokens}")
        print(f"  TTFT   mean {t['mean']:8.1f} ms   p50 {t['p50']:8.1f} ms   p95 {t['p95']:8.1f} ms")
        print(f"  total  mean {tot['mean']:8.1f} ms   p50 {tot['p50']:8.1f} ms   p95 {tot['p95']:8.1f} ms")
        print(f"  TTFT / total = {t['mean'] / tot['mean']:.1%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
