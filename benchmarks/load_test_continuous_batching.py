"""Staggered-arrival inference experiment for vLLM continuous batching.

Start the gateway tunnel in another terminal:
    kubectl port-forward svc/learning-gateway-gateway 8080:8080

Run from the repository root:
    python3 -m pip install --user httpx
    python3 benchmarks/load_test_continuous_batching.py

Three long requests start immediately; two more are submitted after a delay.
The client-side timing shows overlap. To prove *internal* continuous batching,
correlate these timestamps with vLLM scheduler/engine metrics or traces.
"""

import argparse
import asyncio
import time

import httpx


LONG_PROMPTS = [
    "Explain the Kubernetes control plane, scheduler, kubelet, and Service networking in detail. Include practical examples and several paragraphs. /no_think",
    "Explain transformer prefill, autoregressive decoding, KV caching, and why inference performance changes with batch size. Write several detailed paragraphs. /no_think",
    "Explain how a Kubernetes GPU node runs a vLLM Pod, including GPU device plugins, CUDA drivers, scheduling, and resource requests. Write several detailed paragraphs. /no_think",
]
LATE_PROMPTS = [
    "What is the difference between a Pod and a Kubernetes Service? Answer briefly. /no_think",
    "What does an NVIDIA GPU do during LLM inference? Answer briefly. /no_think",
]


async def send_request(client, request_id, prompt, run_start, args, delay=0.0):
    if delay:
        await asyncio.sleep(delay)
    began = time.perf_counter()
    started_at = began - run_start
    print(f"[{started_at:7.3f}s] START  R{request_id} ({'late' if delay else 'initial'})", flush=True)

    payload = {
        "model": args.model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": args.long_tokens if request_id <= 3 else args.short_tokens,
    }
    try:
        response = await client.post(args.url, json=payload)
        response.raise_for_status()
        data = response.json()
        finished_at = time.perf_counter() - run_start
        tokens = data["usage"]["completion_tokens"]
        reason = data["choices"][0]["finish_reason"]
        print(
            f"[{finished_at:7.3f}s] FINISH R{request_id} "
            f"(latency={finished_at-started_at:.3f}s, "
            f"tokens={tokens}, finish={reason})",
            flush=True,
        )
        return {
            "id": request_id, "started": started_at,
            "finished": finished_at, "latency": finished_at - started_at,
            "tokens": tokens, "reason": reason,
        }
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        failed_at = time.perf_counter() - run_start
        print(f"[{failed_at:7.3f}s] FAILED R{request_id}: {exc}", flush=True)
        return None


async def run(args):
    print(
        f"Staggered requests: 3 long at t=0, then 2 short at "
        f"t={args.delay:g}s\n"
        f"Endpoint: {args.url}\n"
    )
    limits = httpx.Limits(max_connections=10, max_keepalive_connections=10)
    async with httpx.AsyncClient(timeout=args.timeout, limits=limits) as client:
        run_start = time.perf_counter()
        tasks = [
            asyncio.create_task(
                send_request(client, i, prompt, run_start, args)
            )
            for i, prompt in enumerate(LONG_PROMPTS, 1)
        ]
        tasks.extend(
            asyncio.create_task(
                send_request(client, i, prompt, run_start, args, delay=args.delay)
            )
            for i, prompt in enumerate(LATE_PROMPTS, 4)
        )
        results = await asyncio.gather(*tasks)
        wall_time = time.perf_counter() - run_start

    completed = [item for item in results if item is not None]
    print("\n========== SUMMARY ==========")
    print(f"Successful requests : {len(completed)}/5")
    print(f"Wall-clock duration : {wall_time:.3f}s")
    print(f"Later arrivals at  : t={args.delay:g}s (approximately)")
    print(f"Total output tokens: {sum(r['tokens'] for r in completed)}")
    if completed:
        print("\nID   START(s) FINISH(s) LATENCY(s) TOKENS")
        for item in sorted(completed, key=lambda r: r["id"]):
            print(
                f"R{item['id']:<3} {item['started']:>7.3f} "
                f"{item['finished']:>9.3f} {item['latency']:>10.3f} "
                f"{item['tokens']:>6}"
            )
        initial_end = max(
            (r["finished"] for r in completed if r["id"] <= 3),
            default=None,
        )
        late_starts = [r["started"] for r in completed if r["id"] >= 4]
        if initial_end is not None and late_starts:
            if min(late_starts) < initial_end:
                print(
                    "\nObservation: Later requests were sent while at "
                    "least one initial request was still active."
                )
            else:
                print(
                    "\nObservation: The initial requests finished "
                    "before later requests were sent. Increase "
                    "--long-tokens or decrease --delay."
                )
    print(
        "\nThis client-side test shows overlapping arrivals and completions, "
        "not proof of vLLM's internal scheduler decisions."
    )
    return 0 if len(completed) == 5 else 1


def main():
    parser = argparse.ArgumentParser(description="Staggered arrival experiment for vLLM")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds until R4/R5 start")
    parser.add_argument("--long-tokens", type=int, default=512)
    parser.add_argument("--short-tokens", type=int, default=80)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument(
        "--url",
        default="http://localhost:8080/v1/chat/completions",
    )
    parser.add_argument("--model", default="Qwen/Qwen3-0.6B")
    args = parser.parse_args()
    if args.delay < 0 or args.long_tokens < 1 or args.short_tokens < 1 or args.timeout <= 0:
        parser.error("delay must be nonnegative; token counts and timeout must be positive")
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
