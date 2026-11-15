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

Two Claude Code hooks in [`.claude/hooks/`](.claude/hooks/) (registered in `.claude/settings.json`) back this up:

- **`guard_destructive.py`** (`PreToolUse`, Bash): parses the **whole** command (`&&`/`;`/`|` chains, newlines, `bash -c '…'`, `eval`, `$(…)` and backticks, `env`/`sudo`/`timeout` wrappers, full paths such as `/usr/local/bin/terraform`) and blocks any simple command that runs `terraform apply/destroy/import/state rm…`, `kubectl delete/apply/scale/patch/rollout restart…`, `helm install/upgrade/uninstall/rollback`, `gcloud … create/delete/update/resize` or IAM-policy changes, `gh secret/variable set|delete`, `gh repo delete`, or a force or delete push. Words that only appear as data, such as commit messages, `grep` patterns, `echo` text and heredoc bodies, are ignored. It exits 2 with a message telling the agent to use the workflows and ask the human. Tests: `tests/hooks/`.
- **`format_python.py`** (`PostToolUse`, Edit/Write): runs `ruff format` and `ruff check --fix` on an edited `.py` file in ruff's scope. If ruff finds something it can't fix, it reports it back to the agent.

If you change `BLOCKED` in the guard, add cases to `tests/hooks/test_guard_destructive.py`, both commands that must be blocked and look-alikes that must not be.

## Working locally (no cluster needed)

These checks need no GPU or cloud access. CI runs the same ones in `.github/workflows/validate.yml`. Python tooling uses [uv](https://docs.astral.sh/uv/) and Python 3.12, matching the gateway image.

```bash
make setup        # uv sync: .venv with gateway deps, pytest, ruff
make check        # ruff lint + format check, gateway tests; helm/terraform checks if installed
make test         # gateway tests only
make fmt          # apply ruff fixes and formatting
make helm-check   # helm lint + template for both charts (needs helm)
make tf-check     # terraform fmt -check + validate, no backend (needs terraform)
```

`helm` and `terraform` may not be installed on the developer's Mac. `make check` says when it skips them. Report that instead of claiming they passed.

**Fake vLLM.** `dev/fake_vllm.py` imitates the vLLM endpoints this repo uses (`/health`, `/v1/models`, `/v1/chat/completions` with and without `"stream": true`, and `/metrics` with `vllm:*` gauges). It has configurable time-to-first-token and per-token delays (`FAKE_VLLM_TTFT_MS`, `FAKE_VLLM_TPOT_MS`). Use it to develop the gateway without a GPU:

```bash
make run-fake-vllm   # terminal 1: fake vLLM on 127.0.0.1:8001
make run-gateway     # terminal 2: gateway on 127.0.0.1:8080 -> fake vLLM, auto-reload
make smoke-local     # terminal 3: tests/smoke_test.py through the local gateway
```

The same two servers are defined in `.claude/launch.json` (`fake-vllm`, `gateway`).

**Gateway tests** (`tests/gateway/`) run the real `helm/gateway/files/main.py` and the fake vLLM as uvicorn servers on free ports and test them over HTTP. Known gaps (streaming passthrough, `/metrics`, request IDs) are `xfail(strict=True)`. When you implement one, its test passes, pytest reports XPASS as a failure, and you must remove the marker.

Fake-vLLM results say nothing about real latency or throughput. Benchmarks need the real cluster.

Against a running cluster, with a port-forward open: `python3 tests/smoke_test.py` (vLLM on :8000 by default, or set `VLLM_BASE_URL=http://localhost:8080` for the gateway).

## Conventions

- **Honest documentation.** The README separates what was verified, when, and under what conditions from what wasn't. Keep that standard: date measurements, label single trials as single trials, and never claim something was tested end to end unless it was. Mark unverified work as unverified.
- **Pin versions.** Container image tags, Helm chart versions, and Python packages are pinned. Keep them pinned when adding new ones.
- **Keep workloads on the right pool.** Only vLLM (and DCGM Exporter, which doesn't request the GPU) goes on `gpu-l4-pool`. Everything else uses `nodeSelector: cloud.google.com/gke-nodepool: cpu-pool`.
- **Workflows that change infrastructure** take a `workflow_dispatch` confirmation string (for example `START_GPU_PLATFORM`, `DESTROY_GKE`, `RUN_BENCHMARK`) and share the `terraform-gke-state` concurrency group. Follow the same pattern for new ones.
- **Gateway changes:** edit `helm/gateway/files/main.py` and run `make check`. The Deployment's checksum annotation restarts the pod when it changes. Add new Python dependencies, with pinned versions, to **both** the `pip install` line in `helm/gateway/templates/deployment.yaml` and `pyproject.toml` (then `uv lock`).
- **Benchmark results** are committed as raw JSON/CSV next to the script that produced them, and summarised in `benchmarks/README.md` and the main README.
- Commit messages are short imperative sentences describing the change (see `git log`).

## Where things are

| Task | Look in |
| --- | --- |
| Cluster, node pools, GPU count | `terraform/main.tf`, `terraform/variables.tf` |
| vLLM args, image, GPU scheduling | `helm/vllm/values.yaml`, `helm/vllm/templates/deployment.yaml` |
| Gateway behaviour | `helm/gateway/files/main.py`, tests in `tests/gateway/` |
| Local dev servers, Python deps | `Makefile`, `pyproject.toml`, `dev/fake_vllm.py` |
| Agent permissions and hooks | `.claude/settings.json`, `.claude/hooks/`, tests in `tests/hooks/` |
| Start / scale / deploy / destroy | `.github/workflows/` (`start-platform.yml` is the all-in-one) |
| Official benchmark pipeline | `.github/workflows/run-final-benchmark.yml`, `benchmarks/final-benchmark/` |
| Monitoring setup and verification | `monitoring/README.md`, `benchmarks/final-benchmark/benchmark-monitoring.md` |
| Roadmap | README section "Further work — scaling and reliability" |
