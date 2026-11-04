# Inference load-testing experiments

This document records initial experiments on the learning-only GKE GPU inference platform. Requests followed:

`Cloud Shell -> kubectl port-forward -> FastAPI gateway (CPU node, :8080) -> learning-vllm Service (:8000) -> vLLM (NVIDIA L4 GPU)`

## Environment

- GPU: 1 x NVIDIA L4 on a GKE `g2-standard-4` node
- CPU: GKE `e2-standard-2` node running the FastAPI gateway
- Model: `Qwen/Qwen3-0.6B`; vLLM serving the OpenAI-compatible chat completions API
- Client: Python `asyncio` + HTTPX, running in Google Cloud Shell through a local port-forward
- Requests sent concurrently with `asyncio.gather()`
- Original identical-prompt script used the message `Explain Kubernetes in one sentence. /no_think`, `temperature=0`, `max_tokens=100`
- Measurements captured on 2026-11-04 and manually transcribed from Cloud Shell results shared during the project

## Completed measurements: identical prompts

The following are *single-run observations*, not statistical averages or a production benchmark.

| Concurrent requests | Wall-clock time (s) | Total output tokens | Aggregate output throughput (tokens/s) |
| ---: | ---: | ---: | ---: |
| 1 | 0.478 | 25 | 52.31 |
| 5 | 0.921 | 125 | 135.73 |
| 10 | 0.895 | 250 | 279.30 |
| 20 | 1.568 | 500 | 318.95 |

An earlier separate five-request run recorded 0.803 s wall-clock, 125 output tokens, and 155.60 output tokens/s. This shows run-to-run variation.

A separate single-request upstream timing measurement printed by FastAPI was **246.10 ms**. This is the time spent awaiting the HTTPX upstream call to vLLM, and should **not** be directly equated with client-side latency or GPU compute-only time.

### Metric definitions

- **Request latency:** elapsed client-side time for one POST and its full response
- **Average latency:** arithmetic mean of successful request latencies
- **Wall-clock time:** elapsed time from starting the batch until all requests finish (including client-side overhead)
- **Aggregate output throughput:** sum of output tokens in successful responses divided by batch wall-clock time; not throughput for any one request
- A single-request result is not a steady-state throughput measurement. Client/network overhead and short outputs materially affect these values.

## Reproducing the benchmarks

From the repository root, with `kubectl` connected to the GKE cluster:

Terminal 1:

```bash
kubectl port-forward svc/learning-gateway-gateway 8080:8080
```

Terminal 2:

```bash
python3 -m pip install --user httpx
python3 benchmarks/load_test.py 1 --mode identical
python3 benchmarks/load_test.py 5 --mode identical
python3 benchmarks/load_test.py 10 --mode identical
python3 benchmarks/load_test.py 20 --mode identical
```

Try different prompts with:

```bash
python3 benchmarks/load_test.py 20 --mode varied
```

The varied-prompt mode has **no recorded measurement yet**. Use it to test a less repetitive workload, while recognizing that prompt lengths and response lengths may differ.

To inspect FastAPI's upstream HTTP timings:

```bash
kubectl logs deployment/learning-gateway-gateway --tail=100 | grep "vLLM request latency"
```

## Interpretation and limitations

Throughput increased with concurrency in these initial runs, consistent with overlapping work and possible continuous batching. The exact internal batching behavior was **not measured**. Since all original prompts were identical, cache reuse may influence results if prefix caching was enabled. No repeated trials, warm-up standardization, token-length matching, GPU utilization measurements, streaming TTFT, p95 latency, or GPU-only timings were collected. The script currently uses non-streaming responses.

For better comparisons: repeat every concurrency setting several times, collect average/p95 latency, use mixed prompt and output lengths, check prefix-caching configuration, and inspect GPU utilization and vLLM metrics.

## Costs and safety

The GKE GPU VM incurs charges while it is running. Scale the GPU node pool back to zero or run the Terraform destroy workflow when experiments finish. The API is intentionally internal to the cluster and exposed to Cloud Shell only through temporary port-forwarding.
