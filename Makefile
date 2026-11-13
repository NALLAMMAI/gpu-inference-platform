# Local development targets. None of these touch GCP, GKE, or Terraform state.
# Python tooling runs through uv (https://docs.astral.sh/uv/).

FAKE_VLLM_PORT ?= 8001
GATEWAY_PORT ?= 8080

.PHONY: help setup test lint fmt helm-check tf-check check run-fake-vllm run-gateway smoke-local

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-15s %s\n", $$1, $$2}'

setup: ## Create .venv with gateway deps, pytest and ruff
	uv sync

test: ## Run gateway tests against the fake vLLM server
	uv run pytest

lint: ## Ruff lint and format check
	uv run ruff check
	uv run ruff format --check

fmt: ## Apply ruff fixes and formatting
	uv run ruff check --fix
	uv run ruff format

helm-check: ## Lint and render both Helm charts (needs helm)
	helm lint helm/vllm
	helm template learning helm/vllm > /dev/null
	helm lint helm/gateway
	helm template learning-gateway helm/gateway > /dev/null

tf-check: ## terraform fmt and validate without a backend (needs terraform)
	terraform -chdir=terraform fmt -check
	terraform -chdir=terraform init -backend=false -input=false > /dev/null
	terraform -chdir=terraform validate

check: lint test ## Everything that needs only Python (lint + test)
	@command -v helm > /dev/null && $(MAKE) helm-check || echo "helm not installed: skipped helm-check"
	@command -v terraform > /dev/null && $(MAKE) tf-check || echo "terraform not installed: skipped tf-check"

run-fake-vllm: ## Fake vLLM on 127.0.0.1:$(FAKE_VLLM_PORT)
	uv run uvicorn fake_vllm:app --app-dir dev --host 127.0.0.1 --port $(FAKE_VLLM_PORT)

run-gateway: ## Gateway on 127.0.0.1:$(GATEWAY_PORT), forwarding to the fake vLLM
	VLLM_BASE_URL=http://127.0.0.1:$(FAKE_VLLM_PORT) \
	  uv run uvicorn main:app --app-dir helm/gateway/files --host 127.0.0.1 --port $(GATEWAY_PORT) --reload

smoke-local: ## Run tests/smoke_test.py against the local gateway
	VLLM_BASE_URL=http://127.0.0.1:$(GATEWAY_PORT) uv run python tests/smoke_test.py
