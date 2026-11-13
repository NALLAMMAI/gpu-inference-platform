# Agent guide: GPU Inference Platform

Instructions for AI coding agents (Claude Code, Codex, Cursor, and others) working in this repository. Humans should start with [README.md](README.md).

## What this project is

A learning and portfolio project that serves an OpenAI-compatible LLM API on Google Kubernetes Engine:

- **Terraform** (`terraform/`) creates a GKE Standard cluster `gpu-inference-learning` in `us-central1-a` with a CPU pool (`cpu-pool`, 1 × e2-standard-2) and an NVIDIA L4 pool (`gpu-l4-pool`, 0 or 1 × g2-standard-4). State lives in a GCS bucket.
- **vLLM** (`helm/vllm/`, release `learning`) serves `Qwen/Qwen3-0.6B` on the L4. Service `learning-vllm:8000`, routes `/v1/models`, `/v1/chat/completions`, `/health`, `/metrics`.
- **FastAPI gateway** (`helm/gateway/`, release `learning-gateway`) runs on the CPU pool and forwards requests to vLLM. Service `learning-gateway-gateway:8080`. Its code is `helm/gateway/files/main.py`, shipped in a ConfigMap and installed with `pip` at pod start (no custom image yet).
- **Monitoring** (`monitoring/`): kube-prometheus-stack (release `monitoring`, namespace `monitoring`), NVIDIA DCGM Exporter on the GPU node, and a vLLM ServiceMonitor. Grafana dashboards are stored as JSON.
- **Benchmarks** (`benchmarks/`): early load tests, plus the official `vllm bench serve` concurrency sweep in `benchmarks/final-benchmark/` (results, Pushgateway publisher, dashboard).
- **GitHub Actions** (`.github/workflows/`) is the only way infrastructure is created, scaled, deployed, benchmarked, or destroyed. Workflows authenticate with OIDC / Workload Identity Federation using repo variables `GCP_PROJECT_ID`, `GCP_WIF_PROVIDER`, `GCP_SERVICE_ACCOUNT`.

All Services are ClusterIP. Access is through `kubectl port-forward` only. The gateway has no authentication.

## Safety rules (cost and destruction)

This project runs on a personal GCP account. A running L4 node costs real money every hour, and the cluster is often deleted between sessions.

- **Never** run `terraform apply`, `terraform destroy`, `gcloud ... create/delete`, `kubectl delete`, `kubectl scale`, `helm install/upgrade/uninstall`, or any command that creates, scales, or deletes cloud resources. These go through the GitHub Actions workflows, which require typed confirmation text. If such an action is needed, explain it and let the human run the workflow.
- Read-only cluster commands (`kubectl get/describe/logs`, `helm list/status`) are fine **if** the human says the cluster is running. Do not assume it exists.
- Never commit `*.tfstate`, `*.tfvars` (except `*.tfvars.example`), `.env`, keys, tokens, or kubeconfig files.
- Do not hardcode the GCP project ID or state bucket name in new files. Use `vars.GCP_PROJECT_ID` in workflows and placeholders in docs.
- Trigger a workflow (`gh workflow run`, `gh run rerun`) only when the human asks for it in the conversation. Before running it, say which workflow it is, its inputs (including the confirmation string), and whether it creates, scales, or destroys billable resources. Never trigger one on your own initiative. Never change repo secrets or variables.

For Claude Code, these rules are enforced in [`.claude/settings.json`](.claude/settings.json): read-only commands are pre-approved; pushes to `main`, merges, workflow runs, `kubectl exec` and `terraform plan` ask first; destructive commands plus reads of state, tfvars, `.env` and credential files are denied. Per-developer overrides go in `.claude/settings.local.json`, which is git-ignored. These rules match commands by text, so they're a guardrail, not a security boundary. Follow the rules above even when a command form isn't covered.

## Working locally (no cluster needed)

These checks need no GPU or cloud access. CI runs the same ones in `.github/workflows/validate.yml`.

```bash
# Terraform formatting and validation (no backend)
terraform -chdir=terraform fmt -check
terraform -chdir=terraform init -backend=false && terraform -chdir=terraform validate

# Helm charts
helm lint helm/vllm && helm template learning helm/vllm > /dev/null
helm lint helm/gateway && helm template learning-gateway helm/gateway > /dev/null

# Python syntax check for gateway, tests, and benchmark scripts
python3 -m py_compile helm/gateway/files/main.py tests/*.py benchmarks/*.py benchmarks/final-benchmark/*.py
```

`helm` and `terraform` may not be installed on the developer's Mac. If they're missing, say so rather than skipping the check silently.

Against a running cluster, with a port-forward open: `python3 tests/smoke_test.py` (vLLM on :8000 by default, or set `VLLM_BASE_URL=http://localhost:8080` for the gateway).

## Conventions

- **Honest documentation.** The README separates what was verified, when, and under what conditions from what wasn't. Keep that standard: date measurements, label single trials as single trials, and never claim something was tested end to end unless it was. Mark unverified work as unverified.
- **Pin versions.** Container image tags, Helm chart versions, and Python packages are pinned. Keep them pinned when adding new ones.
- **Keep workloads on the right pool.** Only vLLM (and DCGM Exporter, which doesn't request the GPU) goes on `gpu-l4-pool`. Everything else uses `nodeSelector: cloud.google.com/gke-nodepool: cpu-pool`.
- **Workflows that change infrastructure** take a `workflow_dispatch` confirmation string (for example `START_GPU_PLATFORM`, `DESTROY_GKE`, `RUN_BENCHMARK`) and share the `terraform-gke-state` concurrency group. Follow the same pattern for new ones.
- **Gateway changes:** edit `helm/gateway/files/main.py`. The Deployment's checksum annotation restarts the pod when it changes. Add new Python dependencies to the `pip install` line in `helm/gateway/templates/deployment.yaml`, with pinned versions.
- **Benchmark results** are committed as raw JSON/CSV next to the script that produced them, and summarised in `benchmarks/README.md` and the main README.
- Commit messages are short imperative sentences describing the change (see `git log`).

## Where things are

| Task | Look in |
| --- | --- |
| Cluster, node pools, GPU count | `terraform/main.tf`, `terraform/variables.tf` |
| vLLM args, image, GPU scheduling | `helm/vllm/values.yaml`, `helm/vllm/templates/deployment.yaml` |
| Gateway behaviour | `helm/gateway/files/main.py` |
| Start / scale / deploy / destroy | `.github/workflows/` (`start-platform.yml` is the all-in-one) |
| Official benchmark pipeline | `.github/workflows/run-final-benchmark.yml`, `benchmarks/final-benchmark/` |
| Monitoring setup and verification | `monitoring/README.md`, `benchmarks/final-benchmark/benchmark-monitoring.md` |
| Roadmap | README section "Further work — scaling and reliability" |
