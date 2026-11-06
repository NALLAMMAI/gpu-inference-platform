#!/usr/bin/env python3
"""Reproduce the November 5, 2026 incident-analysis vLLM benchmark.

Use against a local port-forward for svc/learning-vllm port 8000.
Runs three 60-second closed-loop tests with 300-second cooldowns.
"""
import concurrent.futures
import itertools
import json
import math
import statistics
import threading
import time
import urllib.request
from collections import Counter
from pathlib import Path

URL = "http://127.0.0.1:8000/v1/chat/completions"
MODEL = "Qwen/Qwen3-0.6B"
DURATION = 60
COOLDOWN = 300
CONCURRENCY_LEVELS = [4, 8, 16]
MAX_OUTPUT_TOKENS = 256

INCIDENTS = [
    ("GPU saturation", "GPU utilization increased to 96%, but output throughput remained flat. Increasing concurrent requests from 8 to 24 doubled p95 latency. GPU memory usage stayed stable."),
    ("KV-cache pressure", "KV-cache occupancy climbed from 30% to 93%. Waiting requests increased to 14, and several requests experienced preemption. GPU utilization remained near 85%."),
    ("Slow prefill", "Time to first token increased from 300 ms to 2.8 seconds after average input length increased from 256 to 3000 tokens. Decode throughput remained stable."),
    ("Slow decoding", "Inter-token latency increased from 18 ms to 67 ms after batch size increased. GPU memory bandwidth utilization was high, while compute utilization varied."),
    ("Pending GPU pods", "A new inference Pod remained Pending for 12 minutes. The cluster had an available CPU node, but the GPU node was tainted and already had its GPU allocated."),
    ("GPU out of memory", "A model server crashed with CUDA out-of-memory errors after increasing maximum context length and concurrency. Model weights occupied 8 GiB of GPU memory."),
    ("Low GPU activity", "The inference gateway received 120 requests per minute, but GPU utilization remained below 15%. CPU usage increased sharply, and request parsing became slow."),
    ("Traffic burst", "Request arrival rate tripled in 30 seconds. Running requests reached the configured limit, waiting requests rose to 40, and p95 latency exceeded 10 seconds."),
    ("Uneven replicas", "Three GPU inference replicas had utilization readings of 95%, 42%, and 18%. Requests were routed through one Kubernetes Service, but throughput differed by replica."),
    ("Failed deployment", "A new model image caused readiness probes to fail. The previous version served requests successfully. The updated image changed startup arguments and model-loading behavior."),
    ("GPU temperature", "GPU temperature increased from 48 to 78 degrees Celsius during sustained inference. Power consumption rose, and GPU clocks decreased intermittently."),
    ("Missing metrics", "GPU metrics disappeared from Grafana after a Helm upgrade. The DCGM Exporter Pod was healthy, but Prometheus reported no matching active scrape targets."),
    ("Gateway timeouts", "FastAPI returned HTTP 504 during inference bursts. The gateway timeout was 30 seconds, while some vLLM requests took more than 40 seconds to finish."),
    ("Low prefix-cache benefit", "Prefix caching was enabled, but cache hit rates remained low. Most incoming requests had different system prompts and long, unique documents."),
    ("Concurrency plateau", "Increasing concurrent clients from 8 to 16 improved throughput, but increasing from 16 to 32 gave little additional throughput and doubled p95 latency."),
    ("High inference costs", "A dedicated GPU instance ran continuously, but demand was concentrated into four hours per day. GPU utilization averaged 12% across the full day.")
]

BACKGROUND = """
The service runs inside a Kubernetes cluster with a CPU node pool
and an NVIDIA L4 GPU node pool. Requests enter through a FastAPI
gateway, pass through a Kubernetes ClusterIP Service, and reach
a vLLM inference server.

The vLLM server supports continuous batching and maintains a
GPU-resident KV cache. Model weights, runtime allocations, and
preallocated KV-cache memory share the available GPU VRAM.

The platform uses Prometheus and Grafana to collect GPU utilization,
GPU memory usage, GPU power, temperature, running requests, waiting
requests, and KV-cache occupancy.

The monitoring system samples these metrics periodically, so
short-lived spikes may not appear in every graph. Request-level
timings are available separately.

Infrastructure changes are deployed using Helm and Terraform.
GPU scheduling depends on resource requests, node selectors,
taints, tolerations, and NVIDIA device-plugin availability.

Application reliability depends on startup probes, readiness
checks, capacity management, request timeouts, and correct
service discovery.

For incident analysis, distinguish symptoms from root causes.
Do not assume that high GPU utilization always means efficient
inference, or that allocated GPU VRAM equals active KV-cache usage.

Consider both GPU hardware and the model-serving scheduler.
"""

def make_prompt(index):
    title, observations = INCIDENTS[index]
    return f"""
Incident {index + 1}: {title}

Observed production symptoms:
{observations}

System background:
{BACKGROUND}

Your task:
1. Identify the two most likely causes of this incident.
2. Explain which observations support each cause.
3. Recommend three concrete diagnostic checks.
4. Suggest two mitigations and explain their tradeoffs.
5. State which additional measurement would best validate your conclusion.

Give a concise, technically grounded incident analysis.
Do not invent measurements that were not supplied.
"""

PROMPTS = [make_prompt(i) for i in range(len(INCIDENTS))]

def percentile(values, p):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p * len(ordered)) - 1)]

def run_test(workers):
    sequence = itertools.cycle(range(len(PROMPTS)))
    lock = threading.Lock()
    prompt_counts = Counter()
    deadline = time.monotonic() + DURATION

    def worker(worker_id):
        records, errors = [], []
        while time.monotonic() < deadline:
            with lock:
                prompt_id = next(sequence)
                prompt_counts[prompt_id] += 1
            payload = json.dumps({
                "model": MODEL,
                "messages": [{"role": "user", "content": PROMPTS[prompt_id]}],
                "max_tokens": MAX_OUTPUT_TOKENS,
                "temperature": 0,
                "chat_template_kwargs": {"enable_thinking": False}
            }).encode()
            request = urllib.request.Request(
                URL, data=payload,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            started = time.monotonic()
            try:
                with urllib.request.urlopen(request, timeout=90) as response:
                    result = json.load(response)
                latency = time.monotonic() - started
                usage = result["usage"]
                records.append({
                    "latency": latency,
                    "input": usage["prompt_tokens"],
                    "output": usage["completion_tokens"]
                })
            except Exception as exc:
                errors.append(str(exc))
        return records, errors

    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(worker, range(workers)))
    elapsed = time.monotonic() - started
    records = [record for rows, _ in results for record in rows]
    errors = [error for _, failures in results for error in failures]
    completed = len(records)
    input_tokens = sum(record["input"] for record in records)
    output_tokens = sum(record["output"] for record in records)
    latencies = [record["latency"] for record in records]
    summary = {
        "workers": workers,
        "elapsed_seconds": round(elapsed, 2),
        "completed_requests": completed,
        "errors": len(errors),
        "requests_per_second": round(completed / elapsed, 2),
        "input_tokens_per_second": round(input_tokens / elapsed, 2),
        "output_tokens_per_second": round(output_tokens / elapsed, 2),
        "average_input_tokens": round(input_tokens / completed, 1) if completed else None,
        "average_output_tokens": round(output_tokens / completed, 1) if completed else None,
        "average_latency": round(statistics.mean(latencies), 3) if latencies else None,
        "p50_latency": round(percentile(latencies, .50), 3) if latencies else None,
        "p95_latency": round(percentile(latencies, .95), 3) if latencies else None,
        "prompt_counts": dict(sorted(prompt_counts.items()))
    }
    print(json.dumps(summary, indent=2), flush=True)
    if errors:
        print("Sample errors:", errors[:3], flush=True)
    return summary

def main():
    print("Final vLLM concurrency benchmark", flush=True)
    print(f"Model: {MODEL}", flush=True)
    print(f"Prompt scenarios: {len(PROMPTS)}", flush=True)
    print(f"Test duration: {DURATION} seconds", flush=True)
    print(f"Cooldown: {COOLDOWN} seconds", flush=True)
    all_results = []
    for workers in CONCURRENCY_LEVELS:
        print(f"\n=== STARTING {workers}-WORKER TEST ===", flush=True)
        all_results.append(run_test(workers))
        if workers != CONCURRENCY_LEVELS[-1]:
            print(f"\nCooling down for {COOLDOWN} seconds before the next test...", flush=True)
            time.sleep(COOLDOWN)
    output = Path(__file__).with_name("final-results-new.json")
    output.write_text(json.dumps(all_results, indent=2))
    print("\n=== FINAL COMPARISON ===")
    print("Workers | Req/s | Output tok/s | p50(s) | p95(s) | Errors")
    for result in all_results:
        print(
            f"{result['workers']:7} | {result['requests_per_second']:5.2f} | "
            f"{result['output_tokens_per_second']:12.2f} | "
            f"{result['p50_latency']} | {result['p95_latency']} | {result['errors']}"
        )
    print(f"\nSaved: {output}")

if __name__ == "__main__":
    main()
