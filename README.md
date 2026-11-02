# GPU Inference Platform

An independent learning project for provisioning an NVIDIA L4 GPU in Google Kubernetes Engine (GKE), deploying vLLM with Helm, and testing OpenAI-compatible inference.

## Architecture

GitHub → Terraform validation → GKE Standard cluster → GPU node pool → Helm release → vLLM → Qwen3-0.6B.

## Project structure

- `terraform/` — GKE cluster and dedicated CPU/GPU node pools
- `helm/vllm/` — private ClusterIP vLLM deployment with GPU scheduling
- `tests/` — HTTP inference smoke test
- `.github/workflows/validate.yml` — Terraform and Helm validation (does **not** apply infrastructure)
- `app/` — placeholder for future application extensions

## Prerequisites

Terraform >=1.6, Google Cloud CLI, kubectl, Helm 3, an enabled GKE API, paid billing, and adequate GPU/global/G2 quotas. GPU capacity is not guaranteed by quota.

## Provision

These commands create **billable** cloud resources. First configure Google Cloud authentication (`gcloud auth application-default login` on a laptop; Cloud Shell typically has credentials).

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars to set your project ID
terraform init
terraform fmt
terraform validate
terraform plan
terraform apply
```

The cluster includes one CPU node. **GPU nodes default to zero** to prevent accidental GPU charges. To request the single NVIDIA L4 node, update `gpu_node_count = 1` in your untracked `terraform.tfvars`, then run `terraform plan` and `terraform apply` again.

```bash
gcloud container clusters get-credentials gpu-inference-learning --zone us-central1-a
kubectl get nodes
kubectl get nodes -o custom-columns='NAME:.metadata.name,GPU:.status.allocatable.nvidia\\.com/gpu'
```

## Deploy vLLM

Only after the GPU node is Ready:

```bash
helm lint helm/vllm
helm upgrade --install learning helm/vllm
kubectl rollout status deployment/learning-vllm --timeout=20m
kubectl port-forward svc/learning-vllm 8000:8000
```

In another terminal:

```bash
python3 tests/smoke_test.py
```

Model weights are cached in ephemeral Pod storage and are re-downloaded if a new Pod starts. The inference API is not publicly exposed.

## CI/CD roadmap

The current GitHub Actions workflow checks Terraform formatting/validation and Helm lint/render. It **does not create or destroy cloud infrastructure**. Later improvements: remote Terraform state in Google Cloud Storage, GitHub OIDC with Workload Identity Federation, approval-gated infrastructure apply/destroy, and separate application deployment.

## Teardown

```bash
cd terraform
terraform destroy
```

Keep the local Terraform state until resources are destroyed; do not commit state or credentials. For shared/repeated use, migrate to a protected remote state backend before enabling GitHub Actions apply. Check the Google Cloud billing console for any separately retained billable resources.

## Security and cost notes

This is a learning environment, not a hardened production platform. Restrict cloud IAM permissions, avoid public load balancers until authentication is configured, pin image digests for reproducibility, set billing alerts, and destroy idle clusters.
