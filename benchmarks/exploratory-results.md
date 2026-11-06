# Additional vLLM load-test history (November 5, 2026)

This page records exploratory **direct-to-vLLM** tests performed from Google Cloud Shell via `kubectl port-forward svc/learning-vllm 8000:8000`. They are distinct from the earlier gateway-based experiments in [benchmarks/README.md](README.md).

Environment: GKE, one NVIDIA L4 GPU (24 GiB VRAM), `Qwen/Qwen3-0.6B` on vLLM, OpenAI-compatible `/v1/chat/completions`, non-streaming requests, `temperature=0`, `max_tokens=256`, thinking disabled, and ~60-second closed-loop tests. One worker sends one request at a time and starts its next request only after the previous response completes. All measurements are **single-run observations** except the explicitly grouped repeated configuration.

## Experiment summary

| Test | Prompt | Workers | Requests | Requests/sec | Output tokens/sec | p95 latency |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | P1 short | 4 | 117 | 1.89 | Not measured | Not measured |
| 2 | P1 short (two runs combined) | 8 | 218–224 | 3.56–3.62 | Not measured | Not measured |
| 3 | P2 repetitive | 8 | 287 | 4.65 | Not measured | Not measured |
| 4 | P2 repetitive + token metrics | 8 | 294 | 4.77 | 673.21 | 2.58 s |
| 5a | P3 incidents | 4 | 108 | 1.78 | 455.35 | 2.359 s |
| 5b | P3 incidents | 8 | 193 | 3.12 | 798.50 | 2.671 s |
| 5c | P3 incidents | 16 | 313 | 5.01 | 1,281.51 | 3.400 s |

Test 2 combines two repeated runs (shown as ranges); Tests 3 and 4 use the same P2 workload pattern but different measurements. P2 and P3 workloads differ and should not be directly compared as controlled concurrency experiments.

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

### P2 detailed measured results (P2, Test 4)

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

### P2 exploratory Grafana observations

During the exploratory load tests, sampled measurements included GPU utilization around 83–85%, allocated VRAM around 20.7 GiB, GPU temperature around 60–62 °C under load (49 °C idle), and power around 71–72 W under load (35 W idle). KV-cache occupancy was sampled at 2.91% in one long-prompt test and 3.72% in a later test; the later snapshot showed seven running requests and zero waiting requests.

These numbers are **individual observations**, not definitive maxima, averages or continuous measurements. Prometheus' ~30-second scrape interval can miss short spikes, and the captured screenshots/series have not all been archived here.

### P3 — incident-analysis prompt pool (completed final benchmark)

A pool of 16 fictional incidents: GPU saturation, KV-cache pressure, slow prefill, slow decoding, Pending GPU Pods, GPU OOM, low GPU activity, traffic bursts, uneven replicas, failed deployment, GPU temperature, missing Prometheus metrics, gateway timeouts, low prefix-cache benefits, concurrency plateau, and high inference cost.

Each prompt includes the incident's distinct symptoms, shared Kubernetes/vLLM infrastructure context, and the task of identifying root causes, proposing diagnostic checks and mitigations. A shared rotation assigns prompts across workers; no worker permanently owns a scenario. As prompts recur, prefix caching may still influence results.

Completed P3 test settings: **4, 8, 16 workers; 60-second target test duration each; 300-second cooldowns**. The actual elapsed times were 60.70, 61.87 and 62.52 seconds, respectively; requests in progress completed after the nominal deadline.

### Final incident-analysis concurrency comparison (P3, Test 5)

All three runs completed successfully. Prompts were selected in a rotating sequence from 16 fictional incidents, each with a shared system-background section. Individual prompts averaged about 388 tokens; this is **shorter than the originally discussed ~800-token goal**. Output was capped at 256 tokens; mean generated tokens per request were 255.9, 256.0 and 256.0, so nearly all responses reached the cap.

| Metric | 4 workers | 8 workers | 16 workers |
| --- | ---: | ---: | ---: |
| Elapsed (s) | 60.70 | 61.87 | 62.52 |
| Successful requests | 108 | 193 | 313 |
| Errors | 0 | 0 | 0 |
| Requests/sec | 1.78 | 3.12 | 5.01 |
| Input tokens/sec | 689.84 | 1,209.34 | 1,941.10 |
| Output tokens/sec | 455.35 | 798.50 | 1,281.51 |
| Average input tokens/request | 387.7 | 387.7 | 387.7 |
| Average output tokens/request | 255.9 | 256.0 | 256.0 |
| Mean end-to-end latency (s) | 2.248 | 2.520 | 3.163 |
| Median/p50 latency (s) | 2.227 | 2.504 | 3.194 |
| p95 latency (s) | 2.359 | 2.671 | 3.400 |

Observed improvement from 4 to 16 workers: **2.81× higher output tokens/sec** and roughly **44% higher p95 latency**; no request errors were recorded. Highest measured aggregate throughput was at 16 workers, but no saturation point was demonstrated at or below 16 workers. One run at each concurrency level does not establish statistical significance.

The test uses a 60-second request-admission window with closed-loop workers; in-flight requests are allowed to complete. A 300-second cooldown between runs helps separate monitoring periods, but does not necessarily clear the vLLM prefix cache. Per-prompt counts were fairly balanced (4 workers: 6–7 each; 8 workers: 12–13 each; 16 workers: 19–20 each). The script runs directly against the vLLM endpoint via port-forward; the CPU FastAPI gateway is not in the measured path.

Because prompts share a substantial common background and the 16 prompts are reused, cache effects can inflate apparent prompt-processing throughput relative to entirely fresh prompts. Nearly all outputs hitting the 256-token cap means this benchmark primarily measures fixed-length generation performance, **not** the quality or completeness of the incident diagnoses. The final-run Grafana screenshot (November 5, 2026; approximately 19:13–19:31) showed three clearly separated load periods. Approximate visually sampled GPU utilization during those periods was 84–87%, 82–85%, and 88% for 4, 8, and 16 workers respectively. The GPU allocated VRAM line remained near 20.7 GiB. Temperature peaked around 60–61 °C and power around 71–73 W while loaded; idle power returned near 35 W and temperature near 49–50 °C between runs. These are visual estimates from Grafana's ~30-second scrape samples, **not** precise time-series maxima. A second screenshot of the same three test windows showed sampled running requests of **4, 8 and 16** respectively, matching configured worker counts. Visually estimated sampled KV-cache occupancy peaks were approximately **1–2%, 2–3% and 5%** respectively. Waiting requests remained **0 at all visible samples**. These are samples, not proof that queues never occurred between 30-second scrapes. Both Grafana screenshots are preserved under `benchmarks/charts/` and embedded below.

### Performance findings and analysis (P3)

#### How to interpret a worker

A worker is **one continuously active client**, loosely analogous to a user making repeated requests. Each worker sends one request, waits for the *entire non-streaming response*, then immediately sends another. Therefore **4, 8 and 16 workers mean up to 4, 8 and 16 concurrent in-flight client requests**—not 4, 8 or 16 registered users.

Unlike a real person, a benchmark worker has no reading or typing pauses. A system could serve many more connected users with only a few simultaneously active requests. Conversely, longer prompts and outputs could put more pressure on the GPU with the same number of workers.

```text
Worker -> send prompt -> vLLM scheduler/GPU -> complete response
       -> immediately send next prompt -> repeat
```

#### Finding A — More concurrent workers increased total throughput

| Concurrent workers | Completed requests | Requests/sec | Aggregate output tokens/sec |
| ---: | ---: | ---: | ---: |
| 4 | 108 | 1.78 | 455.35 |
| 8 | 193 | 3.12 | 798.50 |
| 16 | 313 | 5.01 | 1,281.51 |

With 4 workers, the L4 generated roughly **455 output tokens/sec** across all requests. With 16 workers, it generated about **1,282 output tokens/sec**—**2.81× more aggregate throughput for 4× more concurrent workers**.

Output tokens/sec is **total completion tokens returned in successful requests divided by elapsed wall-clock time**. It is not the per-user token generation rate. Here, almost all requests hit the configured maximum of 256 completion tokens, so request throughput and output token throughput scale together.

**Why might throughput improve?** vLLM supports *continuous batching*: it can schedule multiple active sequences together, improving the amount of useful work accomplished by the GPU. Our measurements are consistent with increased batching efficiency, but this test does **not prove batching alone caused** the throughput gains.

The GPU utilization samples remained roughly 80–90% across these tests while output tokens/sec increased. GPU utilization indicates how often the GPU is busy over the sampled interval, **not** its useful token generation throughput.

#### Finding B — Individual responses became slower

| Concurrent workers | Average latency | p50 latency | p95 latency |
| ---: | ---: | ---: | ---: |
| 4 | 2.248 s | 2.227 s | 2.359 s |
| 8 | 2.520 s | 2.504 s | 2.671 s |
| 16 | 3.163 s | 3.194 s | 3.400 s |

At 4 workers, approximately 95% of successful requests completed within **2.36 seconds**. At 16 workers, p95 increased to **3.40 seconds**, roughly **44% higher**.

**How was this latency measured?** The Python client starts a monotonic timer immediately before the HTTP request and stops it after the full JSON response has been received and parsed. p95 is the nearest-rank 95th percentile of those per-request elapsed times. The measurement includes client and port-forward overhead, vLLM scheduling, prefill and decode; it is **not** GPU kernel time, time to first token (TTFT), or inter-token latency (ITL).

**Interpretation:** The server did more work overall, but individual requests took longer because simultaneous inference sequences share GPU execution and memory-bandwidth capacity. This is the central **throughput–latency tradeoff**.

#### Finding C — No sustained request queue was visible

| Workers | Sampled running requests | Sampled waiting requests |
| ---: | ---: | ---: |
| 4 | 4 | 0 |
| 8 | 8 | 0 |
| 16 | 16 | 0 |

Grafana captured running-request counts corresponding to the configured worker counts, while the waiting-request gauge showed **zero at the visible samples**.

**Interpretation:** vLLM admitted the observed load without a sustained queue visible in monitoring. This does **not** establish zero resource contention or guarantee there was never any waiting: Prometheus scraped approximately every 30 seconds and could miss brief queueing.

#### Finding D — KV-cache capacity was not a visible bottleneck

| Workers | Approximate sampled KV-cache occupancy |
| ---: | ---: |
| 4 | 1–2% |
| 8 | 2–3% |
| 16 | ~5% |

Occupancy increased as more sequences ran concurrently, but remained low even at 16 workers. These figures are **visual estimates from the Grafana screenshot**, not precise exported maxima.

The GPU framebuffer allocation remained near **20.7 GiB**, but that reflects model weights and vLLM allocations, including reserved KV-cache capacity—not active KV-cache occupancy. **Nothing in these samples suggests KV-cache capacity was exhausted** at the tested concurrency levels.

#### How many real users can one L4 support?

**What we actually demonstrated:** One L4 running Qwen3-0.6B served **313 successful requests** with **16 continuously active workers** during **62.52 seconds**, achieving **5.01 requests/sec**, **1,281.51 aggregate output tokens/sec**, and **3.40 s p95 request completion latency**, with **zero errors**.

This is **not a maximum-user-capacity measurement**. A production capacity decision depends on realistic user think time, request arrival rates, prompt/output length distributions, latency targets, uptime requirements, and repeat trials.

As a practical example, suppose the application requires **p95 request completion under 3 seconds** for this specific workload:

| Workers | p95 latency | Meets a hypothetical 3-second p95 target? |
| ---: | ---: | :---: |
| 4 | 2.359 s | Yes |
| 8 | 2.671 s | Yes |
| 16 | 3.400 s | No |

Then **8 workers** would provide the highest measured throughput *among these tested settings* while meeting that hypothetical target. With a 4-second p95 target, 16 workers would deliver more throughput. Neither target was an actual requirement for this experiment.

#### Engineering conclusions

1. **Throughput increased with active concurrency:** from 455.35 to 1,281.51 aggregate output tokens/sec (2.81× from 4 to 16 workers). vLLM's continuous batching is a plausible contributor.
2. **Higher concurrency had a latency cost:** p95 rose from 2.359 to 3.400 seconds (~44%).
3. **KV-cache capacity did not appear constrained:** sampled occupancy was only around 5% at the highest tested load.
4. **The server handled the tested requests with zero errors and no sustained queue visible in sampled metrics.** Brief queueing cannot be ruled out.
5. **We did not determine maximum serving capacity:** output throughput was still rising at 16 workers, and the test did not demonstrate a throughput plateau.

**Important limitations:** Prompts averaged **387.7 input tokens**, not the originally intended ~800. Sixteen distinct incident prompts were rotated but shared a common background and were reused, so prefix caching could affect results. Nearly every output hit the **256-token cap**, which may truncate the diagnosis; this benchmark measures bounded-length serving, not response quality. The test used the direct vLLM Service via Cloud Shell port-forward, excluding the FastAPI gateway, and used **one run per concurrency level** rather than statistical repeat trials.

**Project takeaway:** On one NVIDIA L4 GPU serving Qwen3-0.6B, vLLM increased aggregate inference throughput as concurrency increased from 4 to 16, at the cost of slower responses. Selecting an operating point requires balancing throughput against a target p95 latency, rather than simply choosing the most workers.


### P3 Grafana screenshots — final concurrency tests

The following screenshots were uploaded to the repository and show the three 60-second load periods separated by five-minute cooldowns (November 5, 2026).

### GPU utilization, VRAM, temperature and power

![GPU telemetry during 4-, 8-, and 16-worker tests](charts/gpu-performance.png)

### vLLM KV-cache occupancy and scheduler requests

![vLLM KV-cache, running and waiting requests during 4-, 8-, and 16-worker tests](charts/vllm-scheduler.png)

The graphs are Prometheus samples, not per-request measurements; their peaks may miss values between scrapes.

## Cross-experiment limitations

- Different workloads have different input lengths, output lengths and caching patterns. Requests/sec across P1 and P2 should **not** be used to claim one is more efficient on a like-for-like basis.
- Two short-prompt eight-worker runs showed 3.56 and 3.62 requests/sec, modest run-to-run variation.
- P3 results are now recorded from the completed three-run benchmark on November 5, 2026. They are one run per concurrency level, not repeated trials.
- The 5-minute cooldown separates test periods in Grafana and allows return toward idle, but does not necessarily clear vLLM's prefix cache.
- The benchmark sends directly to the vLLM Service through a port-forward, **not** through the FastAPI gateway. Gateway overhead is not included.
