# Streaming inference benchmarks + Grafana

This extension is **configuration only** until a cluster and vLLM server are running.
It does not change the original monitoring dashboard or old benchmark measurements.

## Data flow

1. `vllm bench serve` sends synthetic *streaming* chat traffic and saves JSON.
2. `publish-metrics.py` generates `summary.csv` and pushes a completed sweep to Pushgateway.
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

## Benchmark run

From a host with vLLM benchmark CLI installed and a port-forward to vLLM:

```bash
kubectl port-forward svc/learning-vllm 8000:8000
# In another terminal, from the repo root:
bash benchmarks/final-benchmark/run-concurrency-sweep.sh
```

The script tests max concurrency 1, 2, 4, 8, 16 and 32 at a fixed prompt count.
`--max-concurrency` limits clients in flight; it is **not actual scheduler batch size**.
It uses streaming results so TPOT is measurable. Random inputs are synthetic,
and model output lengths may differ from the requested length. Warmups and
multiple repetitions are recommended before interpreting trends.

Archive CSV and publish results. Port-forward the Pushgateway Service to localhost
(use the name discovered from the cluster, often `benchmark-pushgateway`):

```bash
kubectl -n monitoring port-forward svc/benchmark-pushgateway 9091:9091
# In a separate terminal, from repo root:
python3 -m pip install prometheus-client
python3 benchmarks/final-benchmark/publish-metrics.py --run-id l4-qwen-run01 \
  --gateway localhost:9091
```

To create CSV without Pushgateway:
```bash
python3 benchmarks/final-benchmark/publish-metrics.py --run-id l4-qwen-run01 --archive-only
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
