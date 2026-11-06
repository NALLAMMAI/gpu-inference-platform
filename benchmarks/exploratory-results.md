# Additional vLLM load-test history (November 5, 2026)

This page records exploratory **direct-to-vLLM** tests performed from Google Cloud Shell via `kubectl port-forward svc/learning-vllm 8000:8000`. They are distinct from the earlier gateway-based experiments in [benchmarks/README.md](README.md).

Environment: GKE, one NVIDIA L4 GPU (24 GiB VRAM), `Qwen/Qwen3-0.6B` on vLLM, OpenAI-compatible `/v1/chat/completions`, non-streaming requests, `temperature=0`, `max_tokens=256`, thinking disabled, and ~60-second closed-loop tests. One worker sends one request at a time and starts its next request only after the previous response completes. All measurements are **single-run observations** except the explicitly grouped repeated configuration.

## Experiment summary

| ID | Prompt / workload | Concurrent workers | Completed requests | Requests/sec | Output tokens/sec |
| --- | --- | ---: | ---: | ---: | ---: |
| 1 | **P1:** Short, identical Kubernetes question | 4 | 117 | 1.89 | Not recorded |
| 2 | **P1:** Short, identical Kubernetes question (**two runs grouped**) | 8 | 218–224 | 3.56–3.62 | Not recorded |
| 3 | **P2:** 40-section repetitive technical prompt; eight topics | 8 | 287 | 4.65 | Not recorded |
| 4 | **P2:** Same repetitive prompt pattern, now with token and latency measurements | 8 | 294 | 4.77 | 673.21 |
| 5 | **P3:** Sixteen incident-analysis prompts, rotated across workers | 4 / 8 / 16 | **Pending** | **Pending** | **Pending** |

**Notes:** Test 2 combines two separate runs of the same configuration; ranges are not averages. Tests 3 and 4 use the same *workload pattern* but were separate executions. The P3 tests use different prompts from P1/P2, so do not interpret cross-workload differences as pure concurrency scaling.

## Prompt definitions

### P1 — short identical prompt

Every worker sends this exact input repeatedly:

```text
Explain how Kubernetes schedules GPU workloads and manages GPU resources.
```

### P2 — long repetitive prompt

For worker ID 0–7, each worker is assigned one topic:

1. Kubernetes GPU scheduling
2. vLLM continuous batching
3. GPU memory allocation
4. Transformer attention
5. KV-cache management
6. Distributed inference
7. Model serving latency
8. GPU performance optimization

Each prompt is generated from the corresponding topic:

```python
context = " ".join(
    f"Section {i}: Explain {topic} with an example involving "
    f"GPU inference infrastructure and performance measurement."
    for i in range(40)
)
prompt = context + "\nSummarize the key ideas."
```

The same prompt is reused by each worker for multiple requests. These repetitions and potential prefix-cache reuse make P2 a synthetic input-length stress test rather than a representative user workload.

### P3 — incident-analysis prompt pool (planned final benchmark)

A pool of 16 fictional incidents: GPU saturation, KV-cache pressure, slow prefill, slow decoding, Pending GPU Pods, GPU OOM, low GPU activity, traffic bursts, uneven replicas, failed deployment, GPU temperature, missing Prometheus metrics, gateway timeouts, low prefix-cache benefits, concurrency plateau, and high inference cost.

Each prompt includes the incident's distinct symptoms, shared Kubernetes/vLLM infrastructure context, and the task of identifying root causes, proposing diagnostic checks and mitigations. A shared rotation assigns prompts across workers; no worker permanently owns a scenario. As prompts recur, prefix caching may still influence results.

Planned P3 test settings: **4, 8, 16 workers; 60 seconds each; 300-second cooldowns**. Results should be added only when the three runs are complete and their output is captured.

## More detailed measured results (P2, Test 4)

| Metric | Value |
| --- | ---: |
| Workers | 8 |
| Elapsed | 61.6 s |
| Completed requests | 294 |
| Errors | 0 |
| Input tokens | 237,766 |
| Output tokens | 41,461 |
| Requests/sec | 4.77 |
| Input tokens/sec | 3,860.66 |
| Output tokens/sec | 673.21 |
| Mean end-to-end latency | 1.66 s |
| Median (p50) latency | 1.59 s |
| p95 latency | 2.58 s |
| Average output tokens/request | 141.0 |

These are **aggregate** throughput measurements across concurrent requests, not the per-request decoding speed. Input tokens/sec counts submitted prompt tokens, not necessarily fresh GPU prefill computations when prefix caching is enabled.

## Selected Grafana observations

During the exploratory load tests, sampled measurements included GPU utilization around 83–85%, allocated VRAM around 20.7 GiB, GPU temperature around 60–62 °C under load (49 °C idle), and power around 71–72 W under load (35 W idle). KV-cache occupancy was sampled at 2.91% in one long-prompt test and 3.72% in a later test; the later snapshot showed seven running requests and zero waiting requests.

These numbers are **individual observations**, not definitive maxima, averages or continuous measurements. Prometheus' ~30-second scrape interval can miss short spikes, and the captured screenshots/series have not all been archived here.

## Interpretation and limitations

- Different workloads have different input lengths, output lengths and caching patterns. Requests/sec across P1 and P2 should **not** be used to claim one is more efficient on a like-for-like basis.
- Two short-prompt eight-worker runs showed 3.56 and 3.62 requests/sec, modest run-to-run variation.
- The P3 results are pending; **do not fill them with assumed values**.
- The 5-minute cooldown separates test periods in Grafana and allows return toward idle, but does not necessarily clear vLLM's prefix cache.
- The benchmark sends directly to the vLLM Service through a port-forward, **not** through the FastAPI gateway. Gateway overhead is not included.

