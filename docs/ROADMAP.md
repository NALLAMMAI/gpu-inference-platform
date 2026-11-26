# Roadmap: bulk code editing with a self-hosted coder model

Status as of **November 26, 2026**: planned. Nothing below has been run yet unless a stage's write-up says so.

## The customer problem

A team has hundreds of Python files that all need the same kind of change, for example "add type hints", "replace deprecated API X with Y" or "add input validation". Doing it by hand is slow. Sending the whole codebase to a hosted model API may be too expensive, or not allowed by their data policy.

The proposed solution is a coding model the team hosts itself, on its own GPUs. It applies the instruction to every file in an overnight batch, and the repository's tests check that each edit is correct. The project ends with a recommendation that compares this against a hosted batch API on cost, quality and turnaround time.

## Data

| Dataset | Role in this project |
| --- | --- |
| [InstructCoder](https://huggingface.co/datasets/happylkx/InstructCoder): about 114k Python (instruction, input code, edited code) examples | Realistic request traffic for load tests, then fine-tuning data in Stage 7 |
| EditEval: human-written, execution-based code-editing benchmark | Held-out exam. Scores edit quality; never used for training |
| Real open-source Python repositories with test suites | Stand-in for the customer's code. Their tests decide whether an edit is correct |

## Stages

Each stage follows the same loop: push the system until something breaks or gets too slow, measure it, explain why, fix it, and record the cost of the fix. Each one gets a write-up in `docs/stage-N.md` with the sections **Problem / Observed / Why / Fix / Result / Tradeoff**.

| Stage | Work | Question it answers | GPU |
| --- | --- | --- | --- |
| 0 | Replace `Qwen/Qwen3-0.6B` with `Qwen/Qwen2.5-Coder-7B-Instruct`; load-test with InstructCoder prompts | What changes when a real model nearly fills a 24 GB L4? | 1 × L4 |
| 1 | Raise concurrency until latency targets fail; set an SLO (for example P95 TTFT) | How many concurrent requests can one L4 serve within the SLO? | 1 × L4 |
| 2 | FP8 weights and KV cache, prefix caching, memory and batching settings | How much more capacity comes from configuration alone, and at what quality cost? | 1 × L4 |
| 3 | Try a 14B model; it does not fit in BF16 | 4-bit quantization on one GPU vs tensor parallelism across two | 1–2 × L4 |
| 4 | Two vLLM replicas with KEDA autoscaling | Cold-start time, routing, and what scaling costs | 2 × L4 |
| 5 | Replace request-by-request API calls with an offline batch job (Kubernetes Job, queue, resumable, spot nodes) | How much cheaper is batch per file, and how does it survive preemption? | 1 × L4 |
| 6 | Run edits on real repositories, run their tests, score on EditEval | How good are the edits, measured by tests rather than by eye? | 1 × L4 |
| 7 | Improve quality step by step: prompt, then more context, then a LoRA fine-tune on InstructCoder, then compare with a hosted batch API | When is fine-tuning worth it compared with prompting? | 1 × A100 (spot) |
| 8 | Decision memo: cost per 1,000 files on L4, A100 and a hosted API, plus a demo | What should the customer actually do? | none |

## Related

- Current single-GPU baseline: [Benchmarks and findings](../README.md#benchmarks-and-findings)
- Earlier scaling ideas: [Further work — scaling and reliability](../README.md#further-work--scaling-and-reliability)
