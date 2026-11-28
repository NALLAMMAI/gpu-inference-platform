"""Load-test an OpenAI-compatible endpoint with real code-edit requests from InstructCoder.

Each request asks the model to apply one InstructCoder instruction to one piece of
Python code and return the whole edited file. Requests are streamed so the client
can measure time to first token (TTFT) as well as total latency.

    # Against the local gateway -> fake vLLM (no GPU, checks the script works):
    uv run python benchmarks/instructcoder/load_test.py --concurrency 1,4 --num-requests 8

    # Quick check against the cluster through a port-forwarded gateway (the
    # numbers then include the laptop-to-cluster tunnel, so don't report them):
    kubectl port-forward svc/learning-gateway-gateway 8080:8080
    uv run python benchmarks/instructcoder/load_test.py --concurrency 1 --num-requests 4

    # Reportable numbers: run inside the cluster with the "Run InstructCoder
    # Benchmark" workflow, once against vLLM directly and once through the gateway.

Data: the validation split of likaixin/InstructCoder, pinned to one dataset revision
and downloaded on first use to a local cache (the dataset card states no license, so
the data is not committed to this repository). The validation split is used so the
training split stays unseen for the fine-tuning stage.

Output: one JSON file per concurrency level (a summary plus every request's
measurements) and a summary.csv, in --out. Generated text is not saved unless
--save-outputs is given.

Measured on the client: TTFT and latency include every hop between this script and
vLLM. Run it next to vLLM for the engine's numbers, through the gateway for the
platform's, and compare the two.

Prefix caching: vLLM reuses the KV cache of prompts it has seen. Every level sends the
same requests, so without care later levels would skip most of the prompt work. Each
level therefore sends its own vLLM `cache_salt`, which keeps its cache entries apart
from every other level's and run's (pass --no-cache-salt to turn this off).

Grafana: with --pushgateway, the summary of every finished level is pushed to a
Prometheus Pushgateway, labelled with --run-id, --target and the concurrency.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import random
import statistics
import sys
import time
import uuid
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

DATASET = "likaixin/InstructCoder"
REVISION = "6a778a720284d6520b56bd03d5c3070930d41071"
SPLIT_FILE = "valid.json"
DATA_URL = f"https://huggingface.co/datasets/{DATASET}/resolve/{REVISION}/{SPLIT_FILE}"
PUSH_JOB = "instructcoder_benchmark"
DEFAULT_CACHE = (
    Path.home() / ".cache" / "gpu-inference-platform" / f"instructcoder-{REVISION[:12]}-valid.json"
)

SYSTEM_PROMPT = (
    "You are a code editing assistant. Apply the instruction to the Python code. "
    "Reply with the complete edited code in one ```python code block and nothing else."
)


@dataclass
class Result:
    example_index: int
    ok: bool
    status: int | None
    error: str | None
    ttft_s: float | None
    latency_s: float
    prompt_tokens: int | None
    completion_tokens: int | None
    finish_reason: str | None
    output: str | None = None


def load_examples(cache: Path) -> list[dict]:
    if not cache.exists():
        print(f"Downloading {DATASET}@{REVISION[:12]} {SPLIT_FILE} to {cache}", file=sys.stderr)
        cache.parent.mkdir(parents=True, exist_ok=True)
        with httpx.Client(follow_redirects=True, timeout=120) as client:
            response = client.get(DATA_URL)
            response.raise_for_status()
        tmp = cache.with_suffix(".tmp")
        tmp.write_bytes(response.content)
        tmp.replace(cache)
    examples = json.loads(cache.read_text())
    if not examples or not {"instruction", "input", "output"} <= examples[0].keys():
        raise SystemExit(f"Unexpected dataset format in {cache}")
    return examples


def build_messages(example: dict) -> list[dict]:
    user = f"Instruction: {example['instruction']}\n\nCode:\n```python\n{example['input']}\n```"
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


async def detect_model(client: httpx.AsyncClient, url: str) -> str:
    response = await client.get(f"{url}/v1/models")
    response.raise_for_status()
    models = [m["id"] for m in response.json().get("data", [])]
    if len(models) != 1:
        raise SystemExit(f"Expected one served model, got {models}; pass --model")
    return models[0]


def build_body(model: str, example: dict, args: argparse.Namespace, cache_salt: str | None) -> dict:
    body = {
        "model": model,
        "messages": build_messages(example),
        "max_tokens": args.max_tokens,
        "temperature": args.temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if cache_salt:
        # vLLM only reuses cached prompt blocks between requests with the same salt.
        body["cache_salt"] = cache_salt
    return body


async def one_request(
    client: httpx.AsyncClient,
    url: str,
    model: str,
    index: int,
    example: dict,
    args: argparse.Namespace,
    cache_salt: str | None,
) -> Result:
    body = build_body(model, example, args, cache_salt)
    start = time.perf_counter()
    ttft = None
    usage: dict = {}
    finish_reason = None
    pieces: list[str] = []
    try:
        async with client.stream("POST", f"{url}/v1/chat/completions", json=body) as response:
            if response.status_code != 200:
                text = (await response.aread()).decode(errors="replace")[:300]
                return Result(
                    index,
                    False,
                    response.status_code,
                    text,
                    None,
                    time.perf_counter() - start,
                    None,
                    None,
                    None,
                )
            async for line in response.aiter_lines():
                if not line.startswith("data: ") or line == "data: [DONE]":
                    continue
                chunk = json.loads(line[6:])
                if chunk.get("usage"):
                    usage = chunk["usage"]
                for choice in chunk.get("choices") or []:
                    content = choice.get("delta", {}).get("content")
                    if content:
                        if ttft is None:
                            ttft = time.perf_counter() - start
                        if args.save_outputs:
                            pieces.append(content)
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        return Result(
            index,
            False,
            None,
            f"{type(exc).__name__}: {exc}",
            ttft,
            time.perf_counter() - start,
            None,
            None,
            None,
        )
    return Result(
        example_index=index,
        ok=True,
        status=200,
        error=None,
        ttft_s=ttft,
        latency_s=time.perf_counter() - start,
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        finish_reason=finish_reason,
        output="".join(pieces) if args.save_outputs else None,
    )


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, round(p / 100 * (len(ordered) - 1))))]


def summarize(concurrency: int, results: list[Result], wall_s: float) -> dict:
    ok = [r for r in results if r.ok]
    ttft_ms = [r.ttft_s * 1000 for r in ok if r.ttft_s is not None]
    latency_s = [r.latency_s for r in ok]
    # Time per output token after the first one: the decode speed one user sees.
    tpot_ms = [
        (r.latency_s - r.ttft_s) * 1000 / (r.completion_tokens - 1)
        for r in ok
        if r.ttft_s is not None and r.completion_tokens and r.completion_tokens > 1
    ]
    prompt = [r.prompt_tokens for r in ok if r.prompt_tokens is not None]
    completion = [r.completion_tokens for r in ok if r.completion_tokens is not None]

    def stat(values: list[float], p: float) -> float | None:
        v = percentile(values, p)
        return round(v, 2) if v is not None else None

    finish_reasons = Counter(r.finish_reason for r in ok)
    return {
        "concurrency": concurrency,
        "requests": len(results),
        "succeeded": len(ok),
        "failed": len(results) - len(ok),
        "wall_time_s": round(wall_s, 2),
        "requests_per_s": round(len(ok) / wall_s, 3),
        "output_tokens_per_s": round(sum(completion) / wall_s, 1) if completion else None,
        "ttft_ms_p50": stat(ttft_ms, 50),
        "ttft_ms_p95": stat(ttft_ms, 95),
        "tpot_ms_mean": round(statistics.mean(tpot_ms), 2) if tpot_ms else None,
        "tpot_ms_p95": stat(tpot_ms, 95),
        "latency_s_p50": stat(latency_s, 50),
        "latency_s_p95": stat(latency_s, 95),
        "prompt_tokens_p50": stat(prompt, 50),
        "prompt_tokens_p95": stat(prompt, 95),
        "completion_tokens_p50": stat(completion, 50),
        "completion_tokens_p95": stat(completion, 95),
        # Answers cut off by --max-tokens; a high count means the cap is too low.
        "finish_length": finish_reasons.get("length", 0),
        "finish_reasons": dict(finish_reasons),
    }


def is_label(value: str) -> bool:
    """Safe as a Pushgateway URL path segment and a Prometheus label value."""
    return bool(value) and all(c.isalnum() or c in "-_." for c in value)


def metrics_text(summaries: list[dict]) -> str:
    """Prometheus text format: one gauge per numeric summary field, labelled by concurrency."""
    fields = [
        k
        for k in summaries[0]
        if k != "concurrency" and any(isinstance(s.get(k), int | float) for s in summaries)
    ]
    lines = []
    for field in fields:
        name = f"instructcoder_{field}"
        lines.append(f"# TYPE {name} gauge")
        for s in summaries:
            if isinstance(s.get(field), int | float):
                lines.append(f'{name}{{concurrency="{s["concurrency"]}"}} {s[field]}')
    return "\n".join(lines) + "\n"


async def push_summaries(
    client: httpx.AsyncClient, pushgateway: str, run_id: str, target: str, summaries: list[dict]
) -> None:
    """Replace this run and target's group in the Pushgateway with every level so far."""
    url = f"{pushgateway.rstrip('/')}/metrics/job/{PUSH_JOB}/run_id/{run_id}/target/{target}"
    try:
        response = await client.put(url, content=metrics_text(summaries).encode())
        response.raise_for_status()
    except httpx.HTTPError as exc:
        # The JSON and CSV files are the record; Grafana is a convenience.
        print(f"warning: push to {url} failed: {type(exc).__name__}: {exc}", file=sys.stderr)


async def run_level(
    client: httpx.AsyncClient,
    url: str,
    model: str,
    concurrency: int,
    batch: list[tuple[int, dict]],
    args: argparse.Namespace,
    cache_salt: str | None,
) -> tuple[dict, list[Result]]:
    gate = asyncio.Semaphore(concurrency)

    async def limited(index: int, example: dict) -> Result:
        async with gate:
            return await one_request(client, url, model, index, example, args, cache_salt)

    start = time.perf_counter()
    results = await asyncio.gather(*(limited(i, ex) for i, ex in batch))
    return summarize(concurrency, results, time.perf_counter() - start), results


async def main_async(args: argparse.Namespace) -> int:
    examples = load_examples(args.data_cache)
    rng = random.Random(args.seed)
    sample = rng.sample(range(len(examples)), min(args.sample_size, len(examples)))
    levels = [int(c) for c in args.concurrency.split(",")]
    url = args.url.rstrip("/")
    out = args.out or Path("benchmarks/instructcoder/results") / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True, exist_ok=True)
    run_id = args.run_id or out.name
    if not is_label(run_id):
        raise SystemExit(f"Run id {run_id!r} may only use letters, digits, '-', '_' and '.'; pass --run-id")
    salt_prefix = None if args.no_cache_salt else f"{run_id}-{args.target}-{uuid.uuid4().hex[:8]}"

    def salt(label: str) -> str | None:
        return f"{salt_prefix}-{label}" if salt_prefix else None

    limits = httpx.Limits(max_connections=max(levels) + 4, max_keepalive_connections=max(levels) + 4)
    timeout = httpx.Timeout(args.timeout, connect=10)
    async with httpx.AsyncClient(limits=limits, timeout=timeout) as client:
        model = args.model or await detect_model(client, url)
        print(
            f"Model: {model}  URL: {url}  target: {args.target}  run: {run_id}  "
            f"levels: {levels}  requests/level: {args.num_requests}"
        )

        warmup = [(sample[0], examples[sample[0]])] * args.warmup
        if warmup:
            await run_level(client, url, model, 1, warmup, args, salt("warmup"))

        summaries = []
        for n, concurrency in enumerate(levels):
            # The same requests, in the same order, at every concurrency level.
            batch = [
                (sample[k % len(sample)], examples[sample[k % len(sample)]]) for k in range(args.num_requests)
            ]
            summary, results = await run_level(
                client, url, model, concurrency, batch, args, salt(f"c{concurrency}")
            )
            summaries.append(summary)
            run_info = {
                "run_id": run_id,
                "target": args.target,
                "model": model,
                "url": url,
                "cache_salt": salt(f"c{concurrency}"),
                "dataset": f"{DATASET}@{REVISION}",
                "split": SPLIT_FILE,
                "seed": args.seed,
                "max_tokens": args.max_tokens,
                "temperature": args.temperature,
                "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
            record = {"run": run_info, "summary": summary, "results": [asdict(r) for r in results]}
            (out / f"concurrency-{concurrency}.json").write_text(json.dumps(record, indent=2))
            print(
                f"c={concurrency:>3}  ok={summary['succeeded']}/{summary['requests']}  "
                f"out_tok/s={summary['output_tokens_per_s']}  ttft_p95_ms={summary['ttft_ms_p95']}  "
                f"tpot_mean_ms={summary['tpot_ms_mean']}  latency_p95_s={summary['latency_s_p95']}"
            )
            if args.pushgateway:
                await push_summaries(client, args.pushgateway, run_id, args.target, summaries)
            if args.cooldown and n + 1 < len(levels):
                await asyncio.sleep(args.cooldown)

    columns = [k for k in summaries[0] if k != "finish_reasons"]
    with (out / "summary.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(summaries)
    print(f"Results written to {out}")
    return 0 if all(s["failed"] == 0 for s in summaries) else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--url", default="http://127.0.0.1:8080", help="base URL without /v1 (gateway or vLLM)"
    )
    parser.add_argument("--model", help="model id; default: the single model listed by /v1/models")
    parser.add_argument("--concurrency", default="1,2,4,8,16,32", help="comma-separated concurrency levels")
    parser.add_argument("--num-requests", type=int, default=64, help="requests per concurrency level")
    parser.add_argument("--sample-size", type=int, default=256, help="distinct examples drawn from the split")
    parser.add_argument("--seed", type=int, default=0, help="random seed for choosing examples")
    parser.add_argument("--max-tokens", type=int, default=1024, help="cap on generated tokens per request")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument(
        "--warmup", type=int, default=2, help="unrecorded requests sent before the first level"
    )
    parser.add_argument("--cooldown", type=float, default=0, help="seconds to wait between levels")
    parser.add_argument("--timeout", type=float, default=300, help="per-request read timeout in seconds")
    parser.add_argument("--data-cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--out", type=Path, help="results directory; default: results/<UTC timestamp>")
    parser.add_argument("--save-outputs", action="store_true", help="also store generated text per request")
    parser.add_argument(
        "--target", default="unlabelled", help="what --url points at, e.g. vllm or gateway (a label)"
    )
    parser.add_argument("--run-id", help="label for this run in Prometheus; default: the --out folder name")
    parser.add_argument("--pushgateway", help="Pushgateway base URL, e.g. http://localhost:9091")
    parser.add_argument(
        "--no-cache-salt", action="store_true", help="let levels share vLLM's prefix cache (not comparable)"
    )
    args = parser.parse_args(argv)
    if not is_label(args.target):
        parser.error("--target may only use letters, digits, '-', '_' and '.'")
    return args


def main() -> int:
    return asyncio.run(main_async(parse_args()))


if __name__ == "__main__":
    sys.exit(main())
