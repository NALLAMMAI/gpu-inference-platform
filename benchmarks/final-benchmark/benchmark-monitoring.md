# Streaming inference benchmarks + Grafana

This directory contains the configuration, metrics publisher and archived results for the **single-NVIDIA-L4** benchmark. The completed run used the existing GKE `learning-vllm` pod; no additional GPU, external self-hosted runner, or benchmark Kubernetes pod was created.

## Data flow

1. `vllm bench serve` sends synthetic *streaming* chat traffic and saves JSON.
2. After **each concurrency test**, `publish-metrics.py` refreshes `summary.csv` and pushes all completed test results to Pushgateway (before the 3-minute cooldown).
3. Prometheus scrapes Pushgateway through the Helm chart's ServiceMonitor.
4. Prometheus also scrapes the existing DCGM Exporter and vLLM /metrics targets.
5. Grafana displays benchmark comparisons and live GPU metrics on one dashboard.

The JSON and CSV are the durable benchmark artifacts. Prometheus metrics are
ephemeral (the current stack is configured for 6h retention), and Pushgateway
does not automatically expire pushed data. Deleting the cluster will remove the
dashboard's live data unless separately persisted/exported.

## Installation when the GKE cluster is recreated

Requires the existing kube-prometheus-stack release `monitoring` and namespace
`monitoring` deployed first, including its ServiceMonitor CRD:

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
helm upgrade --install benchmark-pushgateway prometheus-community/prometheus-pushgateway \
  -n monitoring -f benchmarks/final-benchmark/pushgateway-values.yaml
kubectl -n monitoring get servicemonitor
kubectl -n monitoring get svc
```

The chart creates a ServiceMonitor labeled `release: monitoring`; no additional
ServiceMonitor manifest is required. The Service is ClusterIP-only. Check the
actual Service name with `kubectl get svc` before port-forwarding.

## Benchmark run (one GPU, existing serving pod)

Use [Run Final vLLM Benchmark](../../.github/workflows/run-final-benchmark.yml) in **GitHub Actions** and enter `RUN_BENCHMARK`. The job runs on a standard GitHub-hosted **CPU runner** to authenticate to GKE, install/update monitoring, and orchestrate commands.

The benchmark client is **not running on the GitHub runner** and **not on an external GPU**. Instead, the workflow calls `kubectl exec` to run `vllm bench serve` inside the existing `learning-vllm` pod, alongside the inference server on the **same NVIDIA L4 node**. It sends requests to `http://127.0.0.1:8000/v1/chat/completions` within that pod. The client and server can compete for the pod/node's CPU and RAM; only the server runs model inference on the L4.

The workflow sweeps concurrency **1, 2, 4, 8, 16, 32**, with 128 synthetic requests (target 512 input and 256 output tokens) at each level and 180-second cooldowns. After each level it copies the JSON result to the GitHub-hosted runner, invokes [`publish-metrics.py`](publish-metrics.py) **on that CPU runner**, updates `summary.csv`, and publishes aggregate metrics through a localhost `kubectl port-forward` to the in-cluster Pushgateway. Pushgateway/Prometheus/Grafana run in the monitoring stack (preferably on the CPU node), while DCGM Exporter runs on the GPU node to read local GPU telemetry without allocating another GPU.

The completed JSON and CSV are archived under [`results/`](results/) and uploaded as GitHub Actions artifacts. The optional [`run-concurrency-sweep.sh`](run-concurrency-sweep.sh) provides the same CLI benchmark loop for an already-configured environment, but it is **not used by the current GitHub Actions workflow** and does not provision hardware. Do not install a separate GPU or self-hosted runner for this experiment.

`--max-concurrency` limits client requests in flight, not the internal vLLM GPU batch size. Measurements are synthetic; output lengths may vary. Warm-ups and repeat trials are recommended for more reliable comparisons.

### Metrics publishing without a benchmark rerun

When the monitoring stack is running, forward Pushgateway from a trusted machine with `kubectl` access:

```bash
kubectl -n monitoring port-forward svc/benchmark-pushgateway-prometheus-pushgateway 9091:9091
```

Then, from the repository root, publish the already-recorded results (requires `prometheus-client`):

```bash
python3 -m pip install prometheus-client
python3 benchmarks/final-benchmark/publish-metrics.py \
  --results-dir benchmarks/final-benchmark/results \
  --run-id l4-qwen-run01 \
  --gateway localhost:9091
```

To regenerate just the CSV from archived JSON without publishing metrics:

```bash
python3 benchmarks/final-benchmark/publish-metrics.py \
  --results-dir benchmarks/final-benchmark/results \
  --run-id l4-qwen-run01 --archive-only
```

### Validate in Prometheus

Example instant queries:

```promql
inference_benchmark_mean_tpot_ms{run_id="l4-qwen-run01"}
inference_benchmark_p95_tpot_ms{run_id="l4-qwen-run01"}
inference_benchmark_output_tokens_per_second{run_id="l4-qwen-run01"}
```

Use **instant** queries for one point per concurrency value. The new dashboard
includes benchmark panels and retains the original seven live GPU/vLLM panels.
Select a single `run_id` when comparing results from multiple experiments.

**Note:** Prometheus receives a separate labeled time series per concurrency
level. Grafana XY scatter plots require converting that `concurrency` label to a
numeric X field; verify and adjust the panel's transformation after importing
the Grafana v2 dashboard on the target Grafana version.

## Cleanup

Pushgateway retains data until replaced or deleted. To delete one sweep:

```bash
curl -X DELETE http://localhost:9091/metrics/job/inference_benchmark/run_id/l4-qwen-run01
```

Do not publish the Pushgateway write endpoint publicly. If test iterations are
shorter than the Prometheus scrape interval, pushing the completed sweep avoids
losing the short-lived run's aggregate metrics. Preserve the JSON/CSV regardless.
