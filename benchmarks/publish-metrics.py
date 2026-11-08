#!/usr/bin/env python3
"""Archive vLLM bench serve outputs in CSV and push a complete sweep to Pushgateway.

Run after the benchmark sweep. Results stay archived in JSON and CSV even if
Prometheus/Pushgateway are later destroyed. Requires: pip install prometheus-client

Example:
  python3 benchmarks/publish-metrics.py --results-dir benchmarks/results \
    --run-id l4-qwen-20261108 --gateway localhost:9091

Security: port-forward Pushgateway locally; do not expose it publicly.
"""
import argparse
import csv
import json
import math
from pathlib import Path

from prometheus_client import CollectorRegistry, Gauge, push_to_gateway

METRICS = {
    "mean_tpot_ms": ("inference_benchmark_mean_tpot_ms", "Mean time per output token in ms"),
    "p95_tpot_ms": ("inference_benchmark_p95_tpot_ms", "p95 time per output token in ms"),
    "mean_ttft_ms": ("inference_benchmark_mean_ttft_ms", "Mean time to first token in ms"),
    "p95_ttft_ms": ("inference_benchmark_p95_ttft_ms", "p95 time to first token in ms"),
    "output_throughput": ("inference_benchmark_output_tokens_per_second", "Aggregate output token throughput"),
    "request_throughput": ("inference_benchmark_requests_per_second", "Completed requests per second"),
}
FIELDNAMES = ["run_id", "concurrency", *METRICS.keys()]

def extract(raw, key):
    value = raw.get(key)
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"Nonfinite {key} in benchmark output")
    return number

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", type=Path, default=Path("benchmarks/results"))
    ap.add_argument("--run-id", required=True, help="Stable unique ID for this sweep")
    ap.add_argument("--gateway", default="localhost:9091", help="Pushgateway host:port")
    ap.add_argument("--csv", type=Path, default=None)
    ap.add_argument("--archive-only", action="store_true", help="Write CSV without pushing")
    args = ap.parse_args()

    rows = []
    for path in sorted(args.results_dir.glob("concurrency-*.json")):
        concurrency = int(path.stem.split("-")[-1])
        raw = json.loads(path.read_text())
        row = {"run_id": args.run_id, "concurrency": concurrency}
        for key in METRICS:
            row[key] = extract(raw, key)
        if row["mean_tpot_ms"] is None or row["output_throughput"] is None:
            raise ValueError(f"{path}: missing mean_tpot_ms or output_throughput; check vLLM version")
        rows.append(row)
    if not rows:
        raise SystemExit(f"No concurrency-*.json files found in {args.results_dir}")

    rows.sort(key=lambda row: row["concurrency"])
    csv_path = args.csv or args.results_dir / "summary.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Archived {len(rows)} configurations to {csv_path}")

    if args.archive_only:
        return

    registry = CollectorRegistry()
    gauges = {}
    for key, (name, description) in METRICS.items():
        gauges[key] = Gauge(name, description, ["concurrency"], registry=registry)
    for row in rows:
        for key, gauge in gauges.items():
            if row[key] is not None:
                gauge.labels(concurrency=str(row["concurrency"])).set(row[key])

    # Unique Pushgateway grouping key per sweep. Re-running the same sweep replaces
    # those series; different run_ids remain independent until explicitly deleted.
    push_to_gateway(
        args.gateway, job="inference_benchmark",
        grouping_key={"run_id": args.run_id}, registry=registry
    )
    print(f"Published run_id={args.run_id} to {args.gateway}")

if __name__ == "__main__":
    main()
