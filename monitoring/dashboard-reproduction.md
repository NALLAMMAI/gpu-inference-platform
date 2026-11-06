# GPU Inference dashboard and benchmark reproduction

## Original Grafana dashboard

The original **GPU Inference** dashboard is preserved in [`gpu-inference-dashboard.original.json`](gpu-inference-dashboard.original.json). This file was copied from the dashboard JSON provided from the running Grafana instance, not reconstructed from screenshots.

It contains seven time-series panels:
1. NVIDIA L4 GPU utilization
2. NVIDIA L4 VRAM used
3. NVIDIA L4 GPU temperature
4. NVIDIA L4 GPU power consumption
5. vLLM KV-cache usage
6. vLLM running requests
7. vLLM waiting requests

The JSON uses Grafana's **`dashboard.grafana.app/v2` resource schema**, with the original queries, datasource references, visualization options, and layout. It is **configuration only**; it does not preserve historical Prometheus samples.

### Restore the dashboard

The original file is a **Grafana v2 resource JSON**, not the older classic dashboard JSON format. Grafana import support differs across versions, so a classic import dialog may not accept it directly.

On a compatible Grafana version, use its supported resource import/provisioning workflow and make sure the Prometheus datasource referenced as `prometheus` is configured. If the destination only supports classic dashboard JSON, convert/export it in a compatible Grafana environment rather than assuming this file is a classic import artifact.

Verify these job labels match the new Prometheus configuration:
- DCGM Exporter: `job="dcgm-exporter"`
- vLLM: `job="learning-vllm"`

The VRAM query divides `DCGM_FI_DEV_FB_USED` (MiB) by 1024 to display GiB. The KV-cache query multiplies `vllm:kv_cache_usage_perc` by 100 to display percent.

## Re-run the benchmark

With the GKE cluster running and your Kubernetes context configured, open a terminal for the direct vLLM port-forward:

```bash
kubectl port-forward svc/learning-vllm 8000:8000
```

From another terminal at the repository root:

```bash
python3 benchmarks/final-benchmark.py | tee benchmarks/final-run-new.log
```

The benchmark uses 16 fixed incident scenarios and three concurrency levels (4, 8, and 16 workers), each with a 60-second request-admission window and 300-second cooldowns. New results are written to `benchmarks/final-results-new.json`, leaving the recorded historical results untouched.

- [Recorded benchmark data](../benchmarks/final-results.json) — metrics captured from the November 5, 2026 Cloud Shell run.
- [Analysis and Grafana screenshots](../benchmarks/exploratory-results.md) — experiment history, interpretation, limitations, and saved screenshots.

## Before destroying the cluster

The original dashboard **configuration is preserved in GitHub**, so no additional dashboard export is required. The historical Prometheus time series are **not** included in the dashboard JSON. Export any additional time-series measurements you want to keep before deleting the monitoring stack; the committed `stack-values.yaml` configures six-hour retention and no persistent Grafana storage.

