#!/usr/bin/env python3
"""Claude Code PreToolUse hook: block shell commands that change cloud resources.

Second layer behind the deny rules in .claude/settings.json. Those rules match
the command text by prefix, so forms like `bash -c 'terraform destroy'`,
`/usr/local/bin/terraform apply`, `cd terraform && terraform apply`, or
`env X=1 kubectl delete pod p` slip past them. This hook parses the whole
command, splits it into simple commands (on ; && || | & newlines, subshells,
and the argument of `sh -c` / `eval`), resolves the program name, and checks
each simple command against BLOCKED below.

Exit codes (Claude Code hook protocol):
  0  allow (the normal permission rules still apply)
  2  block; stderr is shown to Claude so it can change course

Words that only appear as arguments to another program, such as
`git commit -m "terraform destroy"` or `grep "kubectl delete"`, are not
treated as commands. This is a guardrail against mistakes, not a sandbox.
A determined script (e.g. python -c "subprocess.run(...)") can get around it.

Standard library only, so it runs with any python3.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys

# program -> set of argument words that make the command destructive.
BLOCKED: dict[str, set[str]] = {
    "terraform": {"apply", "destroy", "import", "taint", "untaint", "force-unlock"},
    "tofu": {"apply", "destroy", "import", "taint", "untaint", "force-unlock"},
    "kubectl": {
        "apply",
        "create",
        "delete",
        "replace",
        "patch",
        "edit",
        "scale",
        "autoscale",
        "set",
        "drain",
        "cordon",
        "uncordon",
        "taint",
        "label",
        "annotate",
    },
    "helm": {"install", "upgrade", "uninstall", "delete", "rollback"},
    "gcloud": {"create", "delete", "resize", "update"},
}
# Pairs that are destructive only together: program -> {first: {second, ...}}.
BLOCKED_PAIRS: dict[str, dict[str, set[str]]] = {
    "terraform": {"state": {"rm", "mv", "push", "replace-provider"}},
    "tofu": {"state": {"rm", "mv", "push", "replace-provider"}},
    "kubectl": {"rollout": {"restart", "undo"}},
    "gh": {
        "secret": {"set", "delete", "remove"},
        "variable": {"set", "delete", "remove"},
        "repo": {"delete", "archive"},
        "workflow": {"enable", "disable"},
    },
}
GCLOUD_IAM_PREFIXES = ("add-iam-policy-binding", "remove-iam-policy-binding", "set-iam-policy")

WRAPPERS = {
    "env", "sudo", "nohup", "time", "command", "builtin", "exec", "nice", "stdbuf", "xargs", "caffeinate",
}  # fmt: skip
SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish"}
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1[^\n]*\n.*?\n\s*\2[ \t]*(?=\n|$)", re.S)
SEPARATOR_CHARS = set(";&|()")

GUIDANCE = (
    "Changes to GCP, GKE, Helm releases or Terraform state go through the GitHub Actions "
    "workflows in .github/workflows/, which the human triggers or approves. Explain what is "
    "needed and ask the human instead (see Safety rules in AGENTS.md)."
)


def _strip_heredoc_bodies(command: str) -> str:
    # Heredoc bodies are data (commit messages, file contents), not commands.
    return HEREDOC.sub(lambda m: m.group(0).split("\n", 1)[0], command)


def _tokens(command: str) -> list[str]:
    lexer = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def _simple_commands(tokens: list[str]) -> list[list[str]]:
    commands, current = [], []
    for tok in tokens:
        if tok and set(tok) <= SEPARATOR_CHARS:
            if current:
                commands.append(current)
            current = []
        else:
            current.append(tok)
    if current:
        commands.append(current)
    return commands


def _program_and_args(words: list[str]) -> tuple[str, list[str]]:
    i = 0
    while i < len(words):
        word = words[i]
        if ASSIGNMENT.match(word):
            i += 1
        elif os.path.basename(word) in WRAPPERS:
            i += 1
            # Skip wrapper options such as `env -i`, `nice -n 10`, `timeout 5`.
            while i < len(words) and (words[i].startswith("-") or words[i].isdigit()):
                i += 1
        elif os.path.basename(word) == "timeout":
            i += 2
        else:
            break
    if i >= len(words):
        return "", []
    return os.path.basename(words[i]), words[i + 1 :]


def _check_simple(words: list[str], depth: int) -> str | None:
    program, args = _program_and_args(words)
    if not program:
        return None
    if program in SHELLS:
        for j, arg in enumerate(args[:-1]):
            if arg == "-c" or (arg.startswith("-") and not arg.startswith("--") and "c" in arg):
                return check_command(args[j + 1], depth + 1)
        return None
    if program == "eval":
        return check_command(" ".join(args), depth + 1)

    words_set = set(args)
    hit = words_set & BLOCKED.get(program, set())
    if hit:
        return f"`{program} {sorted(hit)[0]}`"
    for first, seconds in BLOCKED_PAIRS.get(program, {}).items():
        if first in args:
            after = set(args[args.index(first) + 1 :])
            if after & seconds:
                return f"`{program} {first} {sorted(after & seconds)[0]}`"
    if program == "gcloud" and any(a.startswith(GCLOUD_IAM_PREFIXES) for a in args):
        return "`gcloud ... iam-policy`"
    if program == "git" and "push" in args:
        push_args = args[args.index("push") + 1 :]
        if any(a in {"-f", "--force", "--mirror", "--delete"} or a.startswith("--force") for a in push_args):
            return "`git push --force/--delete`"
        if any(a.startswith("+") or a.startswith(":") for a in push_args):
            return "`git push +refspec/:refspec`"
    return None


def check_command(command: str, depth: int = 0) -> str | None:
    """Return a short description of the first destructive command found, else None."""
    if depth > 5:
        return "deeply nested shell command"
    text = _strip_heredoc_bodies(command)
    for inner in re.findall(r"`([^`]*)`", text):
        found = check_command(inner, depth + 1)
        if found:
            return found
    try:
        tokens = _tokens(text)
    except ValueError:
        # Unbalanced quotes: fall back to a plain word scan of each line.
        tokens = []
        for line in text.splitlines():
            tokens += re.findall(r"[^\s;&|()'\"`]+|[;&|()]+", line) + [";"]
    # Command substitutions inside a word, e.g. "$(terraform apply)" or `kubectl delete x`,
    # are checked as commands of their own.
    for tok in tokens:
        for inner in re.findall(r"\$\((.*)\)|`([^`]*)`", tok):
            found = check_command(inner[0] or inner[1], depth + 1)
            if found:
                return found
    for words in _simple_commands(tokens):
        found = _check_simple(words, depth)
        if found:
            return found
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    if payload.get("tool_name") != "Bash":
        return 0
    command = (payload.get("tool_input") or {}).get("command") or ""
    found = check_command(command)
    if found:
        print(
            f"Blocked by .claude/hooks/guard_destructive.py: {found} changes cloud resources "
            f"or repository state. {GUIDANCE}",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
