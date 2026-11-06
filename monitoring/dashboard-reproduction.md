# Monitoring dashboard and benchmark reproduction

## Dashboard provenance

`gpu-inference-dashboard.reconstructed.json` is a **reconstruction**, not an export from the user's running Grafana instance. It reproduces the seven known panel definitions and PromQL queries recorded in the project, but does not preserve exact live panel styling, history, alert rules or panel IDs.

## Import in Grafana

1. Open **Dashboards → New → Import** in Grafana.
2. Upload `monitoring/gpu-inference-dashboard.reconstructed.json`.
3. Select the working **Prometheus** data source for `DS_PROMETHEUS` and import.
4. Confirm that DCGM metrics use `job="dcgm-exporter"` and vLLM metrics use `job="learning-vllm"`. If your scrape labels differ, adjust the PromQL selectors.

The VRAM panel divides `DCGM_FI_DEV_FB_USED` (MiB) by 1024 to show GiB and uses a literal suffix. The vLLM KV-cache panel multiplies a fractional occupancy by 100 to show percent.

## Re-run the benchmark

With GKE context configured, start a port-forward in a dedicated terminal:

```bash
kubectl port-forward svc/learning-vllm 8000:8000
```

In another terminal, from the cloned repository:

```bash
python3 benchmarks/final-benchmark.py | tee benchmarks/final-run-new.log
```

The run uses three 60-second admission windows (4, 8 and 16 workers) separated by 300-second cooldowns. It writes newly observed results to `benchmarks/final-results-new.json` instead of overwriting the historical data in `benchmarks/final-results.json`.

**Recorded results:** [`benchmarks/final-results.json`](../benchmarks/final-results.json) contains the metrics transcribed from the November 5, 2026 Cloud Shell output; no raw request-by-request timings or Prometheus time-series export is claimed.

**Benchmark interpretation:** [`benchmarks/exploratory-results.md`](../benchmarks/exploratory-results.md) includes the main four findings and the original Grafana screenshots.

## Exact original dashboard export (recommended)

If the running cluster and Grafana are still available, additionally export the original dashboard from **Dashboard → Share / Export → Export as JSON** (menu names vary with Grafana version). Save it as `monitoring/gpu-inference-dashboard.original.json` and commit separately. That preserves the precise live panel settings rather than this reconstruction.

Do not destroy the cluster until any desired Prometheus historical data and dashboard configuration have been exported. Prometheus retention in `monitoring/stack-values.yaml` is six hours and persistence is disabled.
