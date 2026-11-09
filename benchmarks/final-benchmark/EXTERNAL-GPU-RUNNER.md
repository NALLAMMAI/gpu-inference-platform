# External GPU runner for the official vLLM benchmark

The **inference server stays in GKE** on the NVIDIA L4 in `gpu-l4-pool`.
The **benchmark client runs on a separate GPU-equipped Linux machine** such as a RunPod GPU Pod. The benchmark uses the official `vllm bench serve` CLI, not a custom HTTP load generator.

## Architecture

```text
GitHub Actions job -> external self-hosted GPU runner (RunPod, etc.)
                          | vllm bench serve
                          | http://127.0.0.1:8000/v1/chat/completions
                          | kubectl port-forward (GCP IAM-authenticated)
                          v
                     GKE: learning-vllm Service -> NVIDIA L4 inference pod

External GPU runner -- local kubectl tunnel on 127.0.0.1:9091 -->
  GKE: benchmark-pushgateway --> Prometheus --> Grafana

External GPU runner --> GitHub Actions artifact (JSON files and summary.csv)
```

**Do not expose** the internal vLLM or Pushgateway services with a public LoadBalancer. Both tunnels use the benchmark runner's loopback address. The runner must reach the GKE API endpoint using its GCP identity, and may need approved master authorized network settings if the control plane is restricted.

## Set up an external GPU machine

1. Create a **separate GPU-equipped Linux x86-64 machine** (for example a RunPod GPU Pod); this is not a new GPU in GKE, so the existing one-GPU GKE quota is unaffected. Choose enough container disk for vLLM and its dependencies. Stop the machine after benchmarking to avoid ongoing billing.
2. Ensure `nvidia-smi` works and install **vLLM 0.11.0** in a GPU-compatible environment so `vllm bench serve` starts with CUDA enabled. The matching inference server uses vLLM 0.11.0. Ensure `python3`, `pip`, `curl`, `git`, and the OS libraries needed by the GitHub Actions runner are available.
3. On GitHub open **Settings -> Actions -> Runners -> New self-hosted runner** for this repository. Select **Linux x64**, follow the exact runner download/configuration instructions GitHub displays, and register the runner with the custom label **`gpu-benchmark`** (for a one-off machine, prefer an ephemeral runner configured with `--ephemeral`).
4. Start the runner with GitHub's documented `./run.sh` command. On the GitHub runners page verify it is **Online** and has labels **`self-hosted`**, **`linux`**, **`x64`**, and **`gpu-benchmark`**.
5. The workflow uses `google-github-actions/auth@v3` and the repository's existing `GCP_PROJECT_ID`, `GCP_WIF_PROVIDER`, and `GCP_SERVICE_ACCOUNT` variables. OIDC federation provides temporary credentials, avoiding downloaded Google service-account keys. Ensure the runner can access GitHub and the Google Cloud APIs and GKE control-plane endpoint.

**Security:** GitHub recommends self-hosted runners for private repositories. Do not accept untrusted pull requests on a privileged runner with access to cloud infrastructure. Prefer a short-lived, dedicated runner and remove its registration after use.

## Run the benchmark

Open **Actions -> Run Final vLLM Benchmark -> Run workflow**, select `main`, and enter `RUN_BENCHMARK`.

The job checks the external GPU, authenticates to GKE, verifies the L4 inference deployment, installs/updates monitoring, and starts two authenticated local `kubectl port-forward` tunnels. It then executes:

- `vllm bench serve --backend openai-chat --endpoint /v1/chat/completions`
- Concurrency: **1, 2, 4, 8, 16, 32**
- 128 requests per level, random 512-token inputs and 256-token outputs
- 180-second cooldown between levels
- JSON per concurrency and `summary.csv` in GitHub Artifacts
- Metrics pushed to Pushgateway for Prometheus and Grafana

## Troubleshooting

- **Workflow queued:** No runner is online with all four required labels.
- **`Failed to infer device type` / `libcuda.so.1` missing:** The runner does not have working NVIDIA GPU/CUDA access inside the benchmark environment. Verify `nvidia-smi` and the vLLM CUDA installation.
- **Unable to get GKE credentials / port-forward:** Check WIF permissions, external GKE API reachability, and GKE authorized networks.
- **No benchmark data in Grafana:** Check Pushgateway ServiceMonitor and Prometheus targets. JSON and CSV are uploaded to the workflow's artifacts if the files were generated.
- **Cannot connect to `/v1/chat/completions`:** Check the vLLM deployment and service in GKE. Do not switch to the completions endpoint when the backend is `openai-chat`.
