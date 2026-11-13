@AGENTS.md

## Claude Code notes

- The shared, tool-neutral project guide is `AGENTS.md` (imported above). Update that file for anything all agents should know; keep this file for Claude-specific notes only.
- Before running any command that touches GCP, GKE, or Terraform state, check the safety rules in `AGENTS.md` and ask the human first.
- When you change behaviour that the README documents (architecture, workflows, benchmark numbers, verification status), update the README in the same change.
