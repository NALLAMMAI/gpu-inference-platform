"""Concurrent HTTP benchmark for the GKE FastAPI -> vLLM inference path.

Requires: pip install httpx
Start: kubectl port-forward svc/learning-gateway-gateway 8080:8080
Run: python3 benchmarks/load_test.py 20 --mode varied
"""

import argparse
import asyncio
import statistics
import time

import httpx

PROMPTS = [
    "Explain Kubernetes.",
    "What is Docker?",
    "What is Terraform?",
    "Explain Python.",
    "What is a GPU?",
    "Explain Linux.",
    "What is a Kubernetes Pod?",
    "Explain CI/CD.",
    "What is a load balancer?",
    "Explain cloud computing.",
    "What is a container?",
    "Explain HTTP.",
    "What is an API?",
    "Explain CUDA.",
    "What is vLLM?",
    "Explain inference.",
    "What is a neural network?",
    "Explain Kubernetes Services.",
    "What is autoscaling?",
    "Explain GPU memory.",
]

IDENTICAL_PROMPT = "Explain Kubernetes in one sentence. /no_think"


async def send_request(client, request_id, args):
    if args.mode == "identical":
        prompt = IDENTICAL_PROMPT
    else:
        prompt = PROMPTS[(request_id - 1) % len(PROMPTS)]
        prompt += " Answer in one sentence. /no_think"

    payload = {
        "model": args.model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": args.max_tokens,
        "temperature": 0,
    }

    start = time.perf_counter()
    try:
        response = await client.post(args.url, json=payload)
        latency = time.perf_counter() - start
        response.raise_for_status()
        result = response.json()

        tokens = int(result["usage"]["completion_tokens"])
        finish_reason = result["choices"][0]["finish_reason"]
        print(
            f"Request {request_id:2d}: {latency:.3f}s | "
            f"{tokens:3d} output tokens | finish={finish_reason}",
            flush=True,
        )
        return latency, tokens
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        print(f"Request {request_id:2d} failed: {exc}", flush=True)
        return None


async def run(args):
    print(f"Sending {args.concurrency} concurrent {args.mode} requests to {args.url}\n")
    async with httpx.AsyncClient(timeout=args.timeout) as client:
        start = time.perf_counter()
        results = await asyncio.gather(
            *(send_request(client, i, args) for i in range(1, args.concurrency + 1))
        )
        wall_time = time.perf_counter() - start

    successful = [r for r in results if r is not None]
    failed = args.concurrency - len(successful)
    if not successful:
        print("\nAll requests failed.")
        return 1

    latencies = [latency for latency, _ in successful]
    output_tokens = sum(tokens for _, tokens in successful)

    print("\n========== RESULTS ==========")
    print(f"Workload mode        : {args.mode}")
    print(f"Concurrent requests  : {args.concurrency}")
    print(f"Successful requests  : {len(successful)}")
    print(f"Failed requests      : {failed}")
    print(f"Wall-clock time      : {wall_time:.3f} s")
    print(f"Average latency      : {statistics.mean(latencies):.3f} s")
    print(f"Minimum latency      : {min(latencies):.3f} s")
    print(f"Maximum latency      : {max(latencies):.3f} s")
    print(f"Total output tokens  : {output_tokens}")
    print(f"Aggregate throughput : {output_tokens / wall_time:.2f} tokens/s")
    print("=============================\n")
    return 0 if failed == 0 else 1


def main():
    parser = argparse.ArgumentParser(description="Concurrent FastAPI/vLLM load test")
    parser.add_argument("concurrency", type=int, nargs="?", default=5)
    parser.add_argument("--mode", choices=("identical", "varied"), default="varied")
    parser.add_argument(
        "--url",
        default="http://localhost:8080/v1/chat/completions",
    )
    parser.add_argument("--model", default="Qwen/Qwen3-0.6B")
    parser.add_argument("--max-tokens", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()
    if args.concurrency < 1 or args.max_tokens < 1 or args.timeout <= 0:
        parser.error("concurrency, max-tokens, and timeout must be positive")
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
