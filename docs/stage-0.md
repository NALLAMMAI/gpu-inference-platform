# Stage 0: a real code model on one L4

Status: **prepared, not yet run on the cluster.** The chart change and the load-test script were tested locally against the fake vLLM on November 27, 2026 (which only shows that the script works, not how fast the model is). "Observed", "Result" and "Tradeoff" are filled in after the cluster run.

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

## How to run it

All billable steps go through the GitHub Actions workflows.

1. Run **Start GPU Inference Platform** (`start-platform.yml`) with confirmation `START_GPU_PLATFORM`. It creates the cluster and the L4 node and deploys this chart. **This starts billing.**
2. Record the cold start and the KV cache size vLLM reports at startup:

   ```bash
   kubectl get pods -l app=learning-vllm -o wide
   kubectl logs deploy/learning-vllm | grep -iE "KV cache|Maximum concurrency|model weights"
   ```

3. Port-forward the gateway and run the load test from the repository root:

   ```bash
   kubectl port-forward svc/learning-gateway-gateway 8080:8080
   uv run python benchmarks/instructcoder/load_test.py --cooldown 60 --out benchmarks/instructcoder/results/l4-qwen2.5-coder-7b-<date>
   ```

   Six levels × 64 requests. If the prediction holds, concurrency 1 alone takes about 9 minutes, and the whole run about 20–40 minutes including cooldowns.

4. Shut down with **Terraform Destroy (GKE)** (`DESTROY_GKE`), or scale the GPU pool to 0.

## Observed

Not run yet.

## Why

Not run yet.

## Result

Not run yet.

## Tradeoff

Not run yet.
