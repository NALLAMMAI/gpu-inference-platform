#!/usr/bin/env python3
"""Claude Code PostToolUse hook: format and lint a Python file after Claude edits it.

Runs `ruff format` and `ruff check --fix` (through uv, using the versions
pinned in pyproject.toml) on the edited file if it's in ruff's scope:
dev/, tests/, helm/gateway/files/, .claude/hooks/. Benchmark scripts are left
as they were run.

Exit codes (Claude Code hook protocol):
  0  done, or nothing to do (non-Python file, out of scope, uv missing)
  2  ruff found problems it could not fix; stderr is shown to Claude

Standard library only, so it runs with any python3.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

SCOPE = ("dev/", "tests/", "helm/gateway/files/", ".claude/hooks/")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    file_path = (payload.get("tool_input") or {}).get("file_path") or ""
    if not file_path.endswith(".py"):
        return 0

    # The repo root is two levels above this file (.claude/hooks/), whatever the session's cwd.
    root = Path(__file__).resolve().parents[2]
    try:
        rel = Path(file_path).resolve().relative_to(root).as_posix()
    except ValueError:
        return 0  # outside the repo
    if not rel.startswith(SCOPE) or not (root / rel).exists():
        return 0

    uv = shutil.which("uv") or str(Path.home() / ".local/bin/uv")
    if not Path(uv).exists():
        return 0

    def ruff(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [uv, "run", "--quiet", "ruff", *args, rel], cwd=root, capture_output=True, text=True, timeout=60
        )

    ruff("format")
    result = ruff("check", "--fix")
    if result.returncode != 0:
        details = result.stdout + result.stderr
        print(f"ruff found issues in {rel} that it could not fix:\n{details}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
