# Stage 0: a real code model on one L4

Status: **prepared, not yet run on the cluster.** The chart change and the load-test script (including its Pushgateway push) were tested locally against the fake vLLM on November 27, 2026; the benchmark workflow and the Grafana dashboard have not run yet (which only shows that the script works, not how fast the model is). "Observed", "Result" and "Tradeoff" are filled in after the cluster run.

## Problem

The platform so far served `Qwen/Qwen3-0.6B`, a general chat model that uses about 1.2 GB of the L4's 24 GB, and was benchmarked with synthetic prompts (random 512 tokens in, 256 out). Neither looks like the workload in the [roadmap](ROADMAP.md): editing Python files on request. Stage 0 swaps in a code model that nearly fills the GPU and drives it with real code-edit requests, to get an honest baseline for later stages.

## What changed

| Change | Why |
| --- | --- |
| Model: `Qwen/Qwen2.5-Coder-7B-Instruct` | Code-specialised 7B model; 4 KV heads, so a small KV cache per token (see below) |
| `--max-model-len` 4096 → 8192, now set in `values.yaml` | A request carries a whole file in and a whole file out |
| `--gpu-memory-utilization 0.90` written out | vLLM's default, made visible because Stage 2 tunes it |
| Model cache (`emptyDir`) 10Gi → 30Gi | The 7B weights are about 15 GB; the old limit would evict the Pod during download |
| `benchmarks/instructcoder/load_test.py` | Sends InstructCoder edits at concurrency 1–32 and records TTFT, time per output token, latency, token counts |
| `run-instructcoder-benchmark.yml` and `instructcoder-dashboard.json` | Runs the load test inside the cluster against vLLM and through the gateway, and shows both in Grafana |
| `tests/smoke_test.py` reads the model id from `/v1/models` | Keeps working whichever model the chart serves |
| `run-final-benchmark.yml` model id | Points the synthetic `vllm bench serve` sweep at the new model |

The vLLM image stays at `v0.11.0`, so the only variable that changes is the model.

## Prediction (before running)

These numbers are worked out from the model config and dataset statistics. The cluster run checks them.

**Does it fit?** 7.6B parameters × 2 bytes (BF16) ≈ **15.2 GB** of weights. vLLM may use 0.90 × 24 GB ≈ 21.6 GB, leaving roughly **5 GB** for the KV cache after weights and working memory.

**KV cache per token.** From `config.json`: 28 layers, 4 KV heads, head size 128.
`2 (K and V) × 28 × 4 × 128 × 2 bytes = 57,344 bytes ≈ 57 KB per token`, so 5 GB holds about **87,000 tokens** across all running requests.

**How big are real requests?** First guess was 800 tokens in + 800 out. The InstructCoder validation split (5,708 examples) says otherwise:

| | Median | 90th percentile | 99th percentile | Max |
| --- | ---: | ---: | ---: | ---: |
| Input code (characters) | 375 | 754 | 1,255 | 2,144 |
| Reference output (characters) | 538 | 1,102 | 1,785 | 2,576 |

At roughly 3.5 characters per token, plus about 60 tokens of system prompt and instruction, a median request holds about **330 tokens** (99th percentile about 950). The KV cache could then hold a few hundred median requests at once. So at concurrency 32 the KV cache should **not** be the limit: compute and memory bandwidth should be.

**Speed for one user.** Each new token needs the full 15.2 GB of weights read from GPU memory. At the L4's roughly 300 GB/s, that is at least **~50 ms per output token** (about 20 tokens/s), versus about 7 ms measured for the 0.6B model on November 8. A median answer of ~150 tokens should take around **8 seconds** at concurrency 1.

**Cold start.** The Pod downloads about 15 GB before it can serve, so expect minutes rather than seconds.

## How the two runs are measured

The load generator runs **inside the vLLM pod**, not on a laptop, so no `kubectl port-forward` tunnel or home internet is in the path. It runs twice with the same seed, so both runs send the same requests:

| Run | Path | What it tells us |
| --- | --- | --- |
| `vllm` | load test → `127.0.0.1:8000` in the same pod | The engine's speed: model + GPU + vLLM settings |
| `gateway` | load test → gateway Service (CPU node) → vLLM Service → vLLM | What a user of the platform gets |

The difference between the two runs is the gateway's cost: one extra network hop, plus a Python proxy limited to 0.5 CPU.

**What is measured, and where:**

- **Client side, by `load_test.py`, per request:** time to first text (TTFT), total time, prompt and output tokens (from vLLM's `usage`), and why it stopped. Per level it computes throughput, p50/p95 TTFT, time per output token, and latency. It writes them to JSON/CSV and pushes them to the Pushgateway as `instructcoder_*{run_id, target, concurrency}`.
- **Server side, by Prometheus, every 15 s during the run:** vLLM's own TTFT histogram, running and waiting requests, KV cache usage, prefix cache hits, DCGM GPU utilisation, and the gateway container's CPU use and throttling.

**Prefix caching.** vLLM keeps the KV cache of prompts it has already seen. Each level and each run sends a different `cache_salt`, so no level reuses another's cached prompts. Without that, every run after the first would look faster than it is. The dashboard's prefix-cache panel should stay low; if it doesn't, the salt isn't working.

**Known limits:**
- The load generator shares the vLLM pod's CPUs with vLLM's API server. That affects both runs the same way, and the client is lightweight (async HTTP), but it isn't free.
- The two runs happen one after the other, not side by side.
- Each run is a single trial.

## How to run it

All billable steps go through the GitHub Actions workflows.

1. Run **Start GPU Inference Platform** (`start-platform.yml`) with confirmation `START_GPU_PLATFORM`. It creates the cluster and the L4 node and deploys vLLM and the gateway. **This starts billing.**
2. Record the cold start and the KV cache size vLLM reports at startup:

   ```bash
   kubectl get pods -l app=learning-vllm -o wide
   kubectl logs deploy/learning-vllm | grep -iE "KV cache|Maximum concurrency|model weights"
   ```

3. Run **Run InstructCoder Benchmark** (`run-instructcoder-benchmark.yml`) with confirmation `RUN_INSTRUCTCODER_BENCHMARK`. The defaults are concurrency `1,2,4,8,16,32`, 64 requests per level, and a 60 s cooldown. It installs or updates Prometheus, Grafana, DCGM Exporter and the Pushgateway, then runs both targets. If the prediction holds, each target takes about 20–40 minutes. It creates no new node, but the GPU node keeps billing while it runs.
4. Watch it in Grafana. Port-forward Grafana, import `benchmarks/instructcoder/instructcoder-dashboard.json` (**Dashboards → New → Import**), and pick the run (`ic-l4-<workflow run id>`):

   ```bash
   kubectl -n monitoring port-forward svc/monitoring-grafana 3000:80
   ```

5. Download the `instructcoder-benchmark-<run id>` artifact from the workflow run. Commit its `vllm/` and `gateway/` folders under `benchmarks/instructcoder/results/` and fill in the sections below.
6. Shut down with **Terraform Destroy (GKE)** (`DESTROY_GKE`), or scale the GPU pool to 0. Pushgateway and Prometheus data go with the cluster; the artifact and committed files are the record.

## Observed

Not run yet.

## Why

Not run yet.

## Result

Not run yet.

## Tradeoff

Not run yet.
