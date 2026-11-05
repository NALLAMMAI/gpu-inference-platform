# GPU node monitoring

This directory prepares **NVIDIA DCGM Exporter** for the existing GKE NVIDIA L4 node pool, `gpu-l4-pool`. It complements vLLM's own Prometheus metrics.

| Source | Metrics | Runs on |
| --- | --- | --- |
| DCGM Exporter | GPU utilization, framebuffer VRAM use, temperature, power (subject to driver/metric support) | GPU node, alongside vLLM |
| vLLM `/metrics` | Running/waiting requests, KV-cache usage, throughput | vLLM process on GPU node |
| Prometheus | Scrapes and stores both sources | Prefer CPU node |
| Grafana | Queries Prometheus for time-series dashboards | Prefer CPU node |

## Why this does not consume a second GPU allocation

The exporter is a DaemonSet restricted with `nodeSelector` to the GKE `gpu-l4-pool`. It tolerates that pool's `nvidia.com/gpu=present:NoSchedule` taint, and deliberately **does not request `nvidia.com/gpu`** as a Kubernetes resource. This lets it monitor the node without taking the GPU resource currently assigned to vLLM. It still uses some CPU and system RAM, and runtime compatibility must be verified.

## Install after Prometheus Operator is installed

The NVIDIA chart has a `ServiceMonitor`, which requires the Prometheus Operator CRD. Install a Prometheus Operator-based stack (for example, `kube-prometheus-stack` with the Helm release name `monitoring` in namespace `monitoring`) first. Ensure that the Prometheus instance selects ServiceMonitors with label `release: monitoring` **across namespaces**, or install the exporter in the selected namespace.

```bash
helm repo add gpu-helm-charts https://nvidia.github.io/dcgm-exporter/helm-charts
helm repo update
helm upgrade --install dcgm-exporter gpu-helm-charts/dcgm-exporter \
  --namespace monitoring --create-namespace \
  -f monitoring/dcgm-values.yaml
kubectl get daemonset,pods -n monitoring -l app.kubernetes.io/instance=dcgm-exporter -o wide
```

The above file path assumes you have cloned this GitHub repository into Cloud Shell and are running from its root. If you have not cloned it, do that before executing Helm with `-f`.

## Verify metrics before configuring dashboards

```bash
kubectl get svc -n monitoring | grep dcgm
```

Find the exporter's service name and port-forward it to local `9400`:

```bash
kubectl -n monitoring port-forward svc/dcgm-exporter 9400:9400
```

In another terminal:

```bash
curl -sS http://127.0.0.1:9400/metrics | grep -E '^DCGM_FI_DEV_(GPU_UTIL|FB_USED|FB_FREE|GPU_TEMP|POWER_USAGE)'
```

Example PromQL expressions for Grafana (only use metrics confirmed present in your exporter):

```promql
DCGM_FI_DEV_GPU_UTIL
DCGM_FI_DEV_FB_USED
DCGM_FI_DEV_GPU_TEMP
DCGM_FI_DEV_POWER_USAGE
```

`DCGM_FI_DEV_FB_USED` is in MiB. The exporter and monitoring stack may need additional troubleshooting on GKE depending on driver and container runtime compatibility. The chart config is **committed but not deployed or verified**; the existing vLLM service has not been changed.

## Cleanup

Destroying the entire GKE cluster with the existing Terraform Destroy workflow also deletes in-cluster exporters and monitoring workloads. Independently retained persistent disks or other separately managed services may incur additional charges.
