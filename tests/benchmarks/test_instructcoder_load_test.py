"""Tests for benchmarks/instructcoder/load_test.py against the fake vLLM server.

They check that the script runs end to end, keeps levels apart in vLLM's prefix
cache, and pushes per-level summaries in a format a Pushgateway accepts. They say
nothing about real latency or throughput.
"""

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

from fastapi import FastAPI, Request

SCRIPT = Path(__file__).resolve().parents[2] / "benchmarks" / "instructcoder" / "load_test.py"
EXAMPLES = [
    {"instruction": f"Rename variable x to y ({i}).", "input": f"x = {i}\nprint(x)", "output": ""}
    for i in range(3)
]


def _load_script():
    spec = importlib.util.spec_from_file_location("instructcoder_load_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look the module up while it loads
    spec.loader.exec_module(module)
    return module


def _fake_pushgateway(received: list):
    app = FastAPI()

    @app.put("/metrics/{path:path}")
    async def put(path: str, request: Request):
        received.append((path, (await request.body()).decode()))
        return {}

    return app


def _args(load_test, tmp_path, *extra):
    data = tmp_path / "valid.json"
    data.write_text(json.dumps(EXAMPLES))
    return load_test.parse_args(
        ["--data-cache", str(data), "--out", str(tmp_path / "out"), "--warmup", "1", *extra]
    )


def test_every_level_gets_its_own_cache_salt(tmp_path):
    load_test = _load_script()
    args = _args(load_test, tmp_path)
    body = load_test.build_body("m", EXAMPLES[0], args, "run-vllm-ab12-c4")
    assert body["cache_salt"] == "run-vllm-ab12-c4"
    assert body["stream"] is True and body["stream_options"] == {"include_usage": True}
    assert "cache_salt" not in load_test.build_body("m", EXAMPLES[0], args, None)


def test_run_writes_results_and_pushes_each_level(tmp_path, fake_vllm_server, fast_vllm, serve):
    load_test = _load_script()
    received = []
    pushgateway = serve(_fake_pushgateway(received))
    args = _args(
        load_test,
        tmp_path,
        "--url", fake_vllm_server.url,
        "--concurrency", "1,2",
        "--num-requests", "3",
        "--max-tokens", "8",
        "--target", "vllm",
        "--run-id", "test-run",
        "--pushgateway", pushgateway.url,
    )  # fmt: skip

    assert asyncio.run(load_test.main_async(args)) == 0

    out = tmp_path / "out"
    levels = [json.loads((out / f"concurrency-{c}.json").read_text()) for c in (1, 2)]
    assert [r["summary"]["succeeded"] for r in levels] == [3, 3]
    salts = {r["run"]["cache_salt"] for r in levels}
    assert len(salts) == 2 and all(s.startswith("test-run-vllm-") for s in salts)
    assert (out / "summary.csv").read_text().startswith("concurrency,")

    # One push per finished level; each replaces the group with every level so far.
    assert [path for path, _ in received] == ["job/instructcoder_benchmark/run_id/test-run/target/vllm"] * 2
    last = received[-1][1]
    assert "# TYPE instructcoder_output_tokens_per_s gauge" in last
    assert 'instructcoder_succeeded{concurrency="1"} 3' in last
    assert 'instructcoder_succeeded{concurrency="2"} 3' in last
    assert "finish_reasons" not in last


def test_metrics_text_keeps_a_field_missing_at_one_level(tmp_path):
    load_test = _load_script()
    text = load_test.metrics_text(
        [
            {"concurrency": 1, "tpot_ms_mean": None, "finish_reasons": {}},
            {"concurrency": 2, "tpot_ms_mean": 51.5, "finish_reasons": {}},
        ]
    )
    assert (
        text == '# TYPE instructcoder_tpot_ms_mean gauge\ninstructcoder_tpot_ms_mean{concurrency="2"} 51.5\n'
    )


def test_failed_push_does_not_fail_the_run(tmp_path, fake_vllm_server, fast_vllm, unused_url, capsys):
    load_test = _load_script()
    args = _args(
        load_test,
        tmp_path,
        "--url", fake_vllm_server.url,
        "--concurrency", "1",
        "--num-requests", "1",
        "--max-tokens", "4",
        "--pushgateway", unused_url,
    )  # fmt: skip

    assert asyncio.run(load_test.main_async(args)) == 0
    assert "warning: push to" in capsys.readouterr().err
