# GPU Inference Platform

A hands-on learning project for deploying an **OpenAI-compatible LLM inference service** on **Google Kubernetes Engine (GKE)**, with infrastructure managed by **Terraform**, applications deployed using **Helm**, and CI/CD orchestrated through **GitHub Actions**.

The platform serves **`Qwen/Qwen3-0.6B`** using **vLLM** on one **NVIDIA L4 GPU**. A **FastAPI gateway** runs separately on a CPU node and forwards inference requests to vLLM over the cluster network.

> **Current infrastructure status (last verified November 4, 2026):** The Terraform Destroy workflow completed successfully, and subsequent checks found no GKE clusters or Compute Engine VMs. The Terraform state bucket, repository, and GitHub-to-GCP authentication configuration were retained. Starting the platform again creates **billable cloud resources**.

## Architecture

```text
Client (Cloud Shell / local laptop)
               |
               | HTTP /v1/chat/completions
               v
     [kubectl port-forward: 8080]
               |
               v
     GKE cluster: gpu-inference-learning
     |
     +-- CPU pool: cpu-pool (1 x e2-standard-2)
     |     |
     |     +-- FastAPI gateway Pod
     |           Service: learning-gateway-gateway:8080
     |                  |
     |                  | Internal Kubernetes HTTP
     |                  v
     +-- GPU pool: gpu-l4-pool (0 or 1 x g2-standard-4)
           |
           +-- vLLM Pod: Qwen/Qwen3-0.6B
                 NVIDIA L4: 24 GB VRAM
                 Service: learning-vllm:8000
                 Routes: /v1/models, /v1/chat/completions, /metrics
```

**Division of responsibilities:** FastAPI receives and forwards API requests; vLLM handles tokenization, inference scheduling, continuous batching, KV-cache management, and response generation; the L4 runs the underlying transformer computations. Services are **ClusterIP/internal**, not publicly exposed.

## Infrastructure

| Component | Configuration |
| --- | --- |
| Cloud | Google Cloud / GKE Standard, `us-central1-a` |
| Cluster | `gpu-inference-learning` |
| CPU node pool | `cpu-pool`: 1 × `e2-standard-2` (2 vCPUs, 8 GB RAM, 50 GB boot disk) |
| GPU node pool | `gpu-l4-pool`: 0 or 1 × `g2-standard-4` (4 vCPUs, 16 GB RAM, 100 GB boot disk) |
| GPU | 1 × NVIDIA L4, 24 GB VRAM when GPU pool is scaled to 1 |
| Model/server | `Qwen/Qwen3-0.6B` via vLLM |
| Applications | FastAPI gateway on CPU pool, vLLM on GPU pool |
| IaC / delivery | Terraform + GCS remote state, GitHub Actions OIDC/WIF, Helm |

The GPU pool can exist with **zero worker nodes**. CPU-only infrastructure still incurs cloud charges; an active L4 node adds GPU/VM charges. GPU quota approval does not guarantee zonal capacity.

## Repository layout

| Path | Purpose |
| --- | --- |
| [`terraform/`](terraform/) | GKE cluster, CPU pool, optional NVIDIA L4 GPU pool, remote-state backend |
| [`helm/vllm/`](helm/vllm/) | vLLM Deployment and internal Service, GPU resource request and scheduling |
| [`helm/gateway/`](helm/gateway/) | FastAPI gateway, ConfigMap-backed application code and internal Service |
| [`benchmarks/`](benchmarks/) | Concurrency, varied-prompt and staggered-arrival load tests |
| [`benchmarks/README.md`](benchmarks/README.md) | Measurements, interpretation, graphs, and reproduction commands |
| [`tests/`](tests/) | Inference smoke-test script |
| [`.github/workflows/`](.github/workflows/) | Validation, provision, scale, deploy, destroy, and combined startup workflows |

## Start the platform

### One workflow (recommended)

Use **[Start GPU Inference Platform](.github/workflows/start-platform.yml)** from **Actions → Run workflow**, on branch `main`. Enter the confirmation text:

```text
START_GPU_PLATFORM
```

The workflow runs these operations in order:

1. Authenticate GitHub Actions to Google Cloud using OIDC and Workload Identity Federation.
2. Initialize Terraform from the GCS remote state and create the GKE cluster, CPU pool (1 node), and GPU pool (**0 nodes**).
3. Apply Terraform again with `gpu_node_count=1` to provision the NVIDIA L4 worker.
4. Configure `kubectl` and wait for both node pools to become Ready.
5. Install the vLLM Helm chart and wait for readiness.
6. Install the FastAPI gateway Helm chart and verify deployments/Services.

**Note:** The combined startup workflow has been committed but had **not been tested end-to-end** as of this README update. It creates billable resources. A failed workflow **does not automatically roll back** infrastructure already created: inspect the run and run the destroy workflow if you are stopping.

### Individual workflows (alternative)

These remain useful for learning and troubleshooting:

| Order | Workflow | Input |
| --- | --- | --- |
| 1 | [Terraform Apply (CPU-only GKE)](.github/workflows/terraform-apply.yml) | `DEPLOY_CPU_ONLY` |
| 2 | [Scale NVIDIA L4 GPU Node Pool](.github/workflows/terraform-scale-gpu.yml) | GPU count `1`; confirmation `SCALE_GPU` |
| 3 | [Deploy vLLM](.github/workflows/deploy-vllm.yml) | `DEPLOY_VLLM` |
| 4 | [Deploy FastAPI Gateway](.github/workflows/deploy-gateway.yml) | Manually dispatch |

**Prerequisites:** A GCP project with billing enabled, the needed GKE/Compute APIs and GPU quotas, the preconfigured GitHub repository variables (`GCP_PROJECT_ID`, `GCP_WIF_PROVIDER`, `GCP_SERVICE_ACCOUNT`), authorized Workload Identity Federation, and access to the existing GCS backend bucket `gpu-inference-tfstate-4643030840`.

### Verify after startup

In Cloud Shell or another environment authenticated to the project:

```bash
gcloud container clusters get-credentials gpu-inference-learning \
  --zone us-central1-a --project project-36527308-968f-4297-96e

kubectl get nodes -o wide
kubectl get pods -o wide
kubectl get svc learning-vllm learning-gateway-gateway
```

For direct inference through the gateway, start a port-forward in one terminal:

```bash
kubectl port-forward svc/learning-gateway-gateway 8080:8080
```

From another terminal, check the forwarded API:

```bash
curl -sS http://localhost:8080/v1/models
```

To verify response generation, run the smoke test or load tests below. Local port-forwarding is a temporary development access method, not a public endpoint.

## Benchmarks and findings

Initial benchmarks from **November 4, 2026** measured client-observed latency and aggregate **output-token** throughput on the L4. Results are individual trials, not statistically robust production benchmarks.

| Concurrency | Identical-prompt aggregate throughput |
| ---: | ---: |
| 1 | 52.31 output tokens/s |
| 5 | 135.73 output tokens/s |
| 10 | 279.30 output tokens/s |
| 20 | **318.95 output tokens/s** |

At concurrency 20 with varied prompts: **20/20 succeeded**, **1.987 s** wall-clock, and **273.76 output tokens/s**. Different prompt/output lengths limit direct comparison with the identical-prompt test.

The **staggered-arrival** experiment sent three longer requests at approximately t=0 and two short requests at t=1 second. The later requests completed while the original three were still active. Samples from vLLM's `/metrics` endpoint showed:

```text
running requests: 0 -> 3 -> 4 -> 3 -> 0
waiting requests: 0 in observed samples
```

This is evidence of **dynamic request admission consistent with continuous batching**; it does not directly measure GPU batch membership at each token step or quantify speedup against static batching.

See the [full benchmark results, caveats, and charts](benchmarks/README.md), including:

- [Throughput vs. concurrency](benchmarks/charts/throughput-vs-concurrency.svg)
- [Average latency vs. concurrency](benchmarks/charts/latency-vs-concurrency.svg)
- [Client-observed staggered request lifetime](benchmarks/charts/continuous-batching-request-lifetime.svg)
- [Sampled vLLM running/waiting requests](benchmarks/charts/continuous-batching-scheduler-metrics.svg)

### Reproduce load tests

With the gateway port-forward running, from the repository root:

```bash
python3 -m pip install --user httpx
python3 benchmarks/load_test.py 5 --mode identical
python3 benchmarks/load_test.py 20 --mode varied
python3 benchmarks/load_test_continuous_batching.py --delay 1
```

To inspect vLLM metrics, use a **separate** port-forward:

```bash
kubectl port-forward svc/learning-vllm 8000:8000
```

Then in another terminal:

```bash
curl -sS http://localhost:8000/metrics | grep -i 'num_requests'
```

## Next milestones

- **GPU monitoring:** `nvidia-smi`, GPU utilization, VRAM use, and comparison at idle versus under load.
- **KV-cache analysis:** vLLM KV-cache capacity/utilization during longer contexts and concurrency.
- **Streaming measurements:** time to first token (TTFT), time per output token (TPOT), p50/p95 latencies.
- **Prometheus + Grafana:** use Helm to deploy metrics collection and dashboards for vLLM; add GPU hardware metrics with a suitable exporter.
- **Capacity and reliability:** gradual load saturation, error handling, readiness/recovery, and safer access controls.
- Save new measurements and charts in [`benchmarks/README.md`](benchmarks/README.md).

Prometheus, Grafana, and detailed GPU monitoring are **planned**, not yet deployed.

## Shut down to control costs

Run **[Terraform Destroy (GKE)](.github/workflows/terraform-destroy.yml)** from GitHub Actions and enter:

```text
DESTROY_GKE
```

It removes the **Terraform-managed** GKE cluster and node pools. When the cluster is deleted, its Pods, Deployments and internal Services disappear as well. It intentionally **does not delete** the externally managed Terraform state bucket, GitHub repository, Workload Identity Federation setup, or standalone resources not in Terraform state.

Verify:

```bash
gcloud container clusters list --project=project-36527308-968f-4297-96e
gcloud compute instances list --project=project-36527308-968f-4297-96e
gcloud compute disks list --project=project-36527308-968f-4297-96e
```

Also inspect any independently created disks, load balancers, reserved IPs, NAT resources or storage, and review Google Cloud Billing (which can report costs with a delay). **Do not manually delete the GCS state bucket** if you intend to resume using the existing Terraform workflows.

## Security notes

This is a learning environment, not a production-hardened deployment. The services are internal, and the FastAPI gateway currently does not implement application-level authentication. Avoid publicly exposing inference endpoints without authentication, use least-privilege IAM, and keep credentials/TF state out of source control. Always shut down unused billable resources.
