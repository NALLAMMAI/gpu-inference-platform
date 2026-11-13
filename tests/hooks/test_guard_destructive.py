"""Tests for .claude/hooks/guard_destructive.py (the PreToolUse command guard)."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / ".claude" / "hooks" / "guard_destructive.py"
spec = importlib.util.spec_from_file_location("guard_destructive", HOOK)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

BLOCKED = [
    # Plain forms (also covered by the deny rules in settings.json)
    "terraform apply",
    "terraform destroy -auto-approve",
    "terraform -chdir=terraform apply",
    "terraform state rm google_container_node_pool.gpu",
    "kubectl delete pod learning-vllm-abc",
    "kubectl -n monitoring delete servicemonitor vllm",
    "kubectl scale deployment learning-vllm --replicas=0",
    "kubectl rollout restart deployment/learning-vllm",
    "helm uninstall learning",
    "helm upgrade --install learning helm/vllm",
    "gcloud container clusters delete gpu-inference-learning --zone us-central1-a",
    "gcloud container node-pools update gpu-l4-pool --cluster c",
    "gcloud projects add-iam-policy-binding p --member m --role r",
    "gh secret set GCP_WIF_PROVIDER",
    "gh repo delete NALLAMMAI/gpu-inference-platform",
    "git push --force origin main",
    "git push origin +main",
    # Forms that get past prefix-matching deny rules
    "/usr/local/bin/terraform apply",
    "~/bin/kubectl delete ns monitoring",
    "cd terraform && terraform apply",
    "make check; terraform destroy",
    "true || helm uninstall learning",
    "kubectl get pods | xargs kubectl delete pod",
    "env TF_LOG=debug terraform apply",
    "TF_IN_AUTOMATION=1 terraform destroy",
    "sudo kubectl delete node n",
    "nohup terraform apply &",
    "timeout 30 kubectl delete pod p",
    "bash -c 'terraform destroy'",
    'sh -c "cd terraform && terraform apply"',
    "bash -lc 'helm uninstall learning'",
    "eval terraform apply",
    'echo "$(terraform apply)"',
    "x=$(helm uninstall learning)",
    "echo `kubectl delete ns x`",
    "cd terraform\nterraform apply",
    "bash -c \"bash -c 'kubectl delete pod p'\"",
]

ALLOWED = [
    "terraform fmt -check",
    "terraform -chdir=terraform validate",
    "terraform plan -destroy",
    "kubectl get pods -o wide",
    "kubectl describe pod learning-vllm-abc",
    "kubectl logs deploy/learning-vllm --tail=50",
    "kubectl port-forward svc/learning-vllm 8000:8000",
    "kubectl config set-context --current --namespace default",
    "helm lint helm/vllm",
    "helm template learning helm/vllm",
    "helm list -A",
    "gcloud container clusters list",
    "gcloud config set project my-project",
    "gh pr create --title 'x' --body 'y'",
    "gh workflow run start-platform.yml -f confirmation=START_GPU_PLATFORM",
    "git push -u origin agent/hooks",
    "make check",
    "uv run pytest",
    # Destructive words as data, not as commands
    'git commit -m "Document terraform destroy workflow"',
    "git commit -m 'Explain why kubectl delete is denied'",
    'grep -n "kubectl delete" README.md',
    "rg 'helm uninstall' docs/",
    "echo terraform apply",
    "git commit -F - <<'EOF'\nBlock terraform apply; kubectl delete && helm uninstall\nEOF",
    "cat <<EOF > notes.md\nrun: cd terraform && terraform destroy\nEOF\necho done",
]


@pytest.mark.parametrize("command", BLOCKED)
def test_blocks(command):
    assert guard.check_command(command), command


@pytest.mark.parametrize("command", ALLOWED)
def test_allows(command):
    assert guard.check_command(command) is None, command


def test_unbalanced_quotes_still_checked():
    assert guard.check_command("echo it's here; terraform apply")
    assert guard.check_command("echo it's fine") is None


def run_hook(payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HOOK)], input=json.dumps(payload), capture_output=True, text=True, timeout=10
    )


def test_hook_protocol_blocks_with_exit_2_and_message():
    result = run_hook({"tool_name": "Bash", "tool_input": {"command": "bash -c 'terraform destroy'"}})
    assert result.returncode == 2
    assert "terraform destroy" in result.stderr
    assert "GitHub Actions" in result.stderr


def test_hook_protocol_allows_with_exit_0():
    result = run_hook({"tool_name": "Bash", "tool_input": {"command": "kubectl get pods"}})
    assert result.returncode == 0
    assert result.stderr == ""


def test_hook_ignores_other_tools_and_bad_input():
    assert run_hook({"tool_name": "Read", "tool_input": {"file_path": "x"}}).returncode == 0
    bad = subprocess.run([sys.executable, str(HOOK)], input="not json", capture_output=True, text=True)
    assert bad.returncode == 0
