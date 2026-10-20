import argparse
import asyncio
import csv
import json
import math
import os
import statistics
import uuid
from datetime import datetime, timezone
from pathlib import Path

from lab import BASE
from manual_replay import part_b_args, e5_args
from shared_prefix_lab import run as run_part_b
from e5_tool_chain import run as run_e5

RUNS = BASE / "runs"
T975 = {2: 12.706, 3: 4.303, 4: 3.182, 5: 2.776, 6: 2.571,
        7: 2.447, 8: 2.365, 9: 2.306, 10: 2.262}
FRACTIONS = (0.1, 0.25, 0.5)


def load(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save(path, value):
    pending = path.with_suffix(path.suffix + ".tmp")
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(pending, path)


def new_session(root, repeats):
    dest = root / ("cross-anchor-qwen-" +
                   datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") +
                   "-" + uuid.uuid4().hex[:6])
    dest.mkdir(parents=True)
    save(dest / "session.json", dict(model="qwen3-8b", repeats=repeats,
         created_utc=datetime.now(timezone.utc).isoformat(),
         source="real vLLM only; no fixtures", execution="sequential",
         methods="Student t 95% CI across independent salted runs; E2/E5 CAF paired by replicate"))
    save(dest / "anchor-index.json", {})
    return dest


def check_run(root, run_id, n, kind):
    path = root / run_id
    metrics = load(path / "attack-metrics.json", {})
    manifest = load(path / "manifest.json", {})
    usage = load(path / "usage.json", [])
    if not (manifest.get("natural_model_run") is True
            and metrics.get("source") == "live vLLM"
            and metrics.get("cold_worker_1") is True
            and metrics.get("one_request_per_worker") is True
            and metrics.get("answer_screen_count") == n
            and len(usage) == n
            and len({r.get("agent") for r in usage}) == n
            and all(isinstance(r.get("cached_tokens"), int) and
                    isinstance(r.get("prompt_tokens"), int) and
                    isinstance(r.get("uncached_tokens"), int) and
                    r["prompt_tokens"] == r["cached_tokens"] + r["uncached_tokens"]
                    for r in usage)):
        raise RuntimeError(f"Live run failed acceptance: {path}")
    if kind == "e5" and not metrics.get("assembly_verified"):
        raise RuntimeError(f"E5 assembly not verified: {path}")
    return metrics


async def execute(session, root):
    settings = load(session / "session.json")
    index_path = session / "anchor-index.json"
    index = load(index_path, {})
    repeats = settings["repeats"]

    async def one(key, n, kind, variant, mark="zwsp_k"):
        if key in index:
            check_run(root, index[key], n, kind)
            print("SKIP", key, index[key], flush=True)
            return
        if kind == "part_b":
            dest = await run_part_b(part_b_args(root, n, variant, mark))
        else:
            dest = await run_e5(e5_args(root, n, variant))
        metrics = check_run(root, dest.name, n, kind)
        index[key] = dest.name
        save(index_path, index)
        print("SAVED", key, dest.name, "hit=", metrics["worker_2_to_n_hit_rate"],
              "uncached=", metrics["uncached_tokens"], flush=True)

    for rep in range(1, repeats + 1):
        for variant in ("none", "prefix"):
            await one(f"e1/r{rep}/{variant}", 5, "part_b", variant)
        for n in (3, 5, 10, 20):
            for variant in ("none", "prefix"):
                await one(f"e2/r{rep}/n{n}/{variant}", n, "part_b", variant)
        for arm in ("const-tool", "nonce-tool", "nonce-late"):
            await one(f"e5/r{rep}/{arm}", 5, "e5", arm)
    # One fresh confirmation per E3 marker. E4 is recomputed from the five
    # paired E1 none/prefix replicates and needs no separate model requests.
    for mark in ("zwsp_k", "short", "sentence"):
        await one(f"e3/{mark}", 5, "part_b", "prefix", mark)
    report(session, root)


def ci(values):
    n = len(values)
    if n < 2:
        raise ValueError("At least two independent runs are needed for a CI")
    mean = statistics.mean(values)
    margin = T975.get(n, 1.96) * statistics.stdev(values) / math.sqrt(n)
    return mean, mean - margin, mean + margin


def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def response_ids(root, run_id, worker):
    obj = load(root / run_id / f"{worker:04d}-worker_{worker}.response.json", {})
    ids = obj.get("prompt_token_ids")
    if not isinstance(ids, list):
        raise RuntimeError(f"Missing server token IDs: {run_id}, worker {worker}")
    verify = load(root / run_id / f"{worker:04d}-worker_{worker}.tokenize.json", {})
    if verify.get("tokens") != ids:
        raise RuntimeError(f"/tokenize IDs disagree with actual request: {run_id}, worker {worker}")
    return ids


def lcp(left, right):
    return next((i for i, (a, b) in enumerate(zip(left, right)) if a != b),
                min(len(left), len(right)))


def report(session, root):
    settings = load(session / "session.json")
    index = load(session / "anchor-index.json", {})
    repeats = settings["repeats"]
    expected = repeats * (2 + 8 + 3) + 3
    if len(index) != expected:
        raise RuntimeError(f"Incomplete anchor: {len(index)}/{expected} runs; report withheld")
    all_ids = list(index.values())
    salts = [load(root / rid / "manifest.json")["cache_salt"] for rid in all_ids]
    if len(set(all_ids)) != expected or len(set(salts)) != expected:
        raise RuntimeError("Run IDs or cache salts are not unique")
    metrics = {}
    for key, rid in index.items():
        n = (int(key.split("/n")[1].split("/")[0]) if key.startswith("e2/")
             else 5)
        metrics[key] = check_run(root, rid, n, "e5" if key.startswith("e5/") else "part_b")

    detailed = []
    summary = dict(model="Qwen3-8B", repeats=repeats, n_runs=expected,
                   evidence_kind="live vLLM; unique salt and cold first worker")

    def add(name, values):
        mean, low, high = ci(values)
        detailed.append(dict(metric=name, n=len(values), mean=mean,
                             ci95_low=low, ci95_high=high, method="Student t"))
        summary[name + "_mean"] = mean
        summary[name + "_ci95_low"] = low
        summary[name + "_ci95_high"] = high

    for variant in ("none", "prefix"):
        entries = [metrics[f"e1/r{r}/{variant}"] for r in range(1, repeats + 1)]
        add(f"e1_{variant}_reusing_hit_rate",
            [m["worker_2_to_n_hit_rate"] for m in entries])
        add(f"e1_{variant}_uncached_tokens", [m["uncached_tokens"] for m in entries])
    for n in (3, 5, 10, 20):
        for fraction in FRACTIONS:
            values = []
            for rep in range(1, repeats + 1):
                none = metrics[f"e2/r{rep}/n{n}/none"]
                front = metrics[f"e2/r{rep}/n{n}/prefix"]
                values.append(front["cost_by_cache_fraction"][str(fraction)] /
                              none["cost_by_cache_fraction"][str(fraction)])
            add(f"e2_n{n}_caf_f{str(fraction).replace('.', '_')}", values)
    for arm in ("const-tool", "nonce-tool", "nonce-late"):
        entries = [metrics[f"e5/r{r}/{arm}"] for r in range(1, repeats + 1)]
        add(f"e5_{arm}_reusing_hit_rate",
            [m["worker_2_to_n_hit_rate"] for m in entries])
        add(f"e5_{arm}_uncached_tokens", [m["uncached_tokens"] for m in entries])
    for arm in ("nonce-tool", "nonce-late"):
        for fraction in FRACTIONS:
            values = []
            for rep in range(1, repeats + 1):
                control = metrics[f"e5/r{rep}/const-tool"]
                attack = metrics[f"e5/r{rep}/{arm}"]
                values.append(attack["cost_by_cache_fraction"][str(fraction)] /
                              control["cost_by_cache_fraction"][str(fraction)])
            add(f"e5_{arm}_caf_f{str(fraction).replace('.', '_')}", values)

    h_values, prompt_values = [], []
    for rep in range(1, repeats + 1):
        front = index[f"e1/r{rep}/prefix"]
        none = metrics[f"e1/r{rep}/none"]
        h_values.append(lcp(response_ids(root, front, 1), response_ids(root, front, 2)))
        prompt_values.append(statistics.mean(
            row["prompt_tokens"] for row in none["workers"][1:]))
    add("H_pre_marker_tokens", h_values)
    add("P_plus_s_full_prompt_tokens", prompt_values)
    add("predicted_collapse_floor", [h / p for h, p in zip(h_values, prompt_values)])
    summary["P_shared_doc_target_tokens"] = 2000
    summary["H_definition"] = "actual /tokenize-verified LCP before per-worker front marker"
    summary["P_plus_s_definition"] = "mean full prompt tokens of E1 none workers 2..5"
    summary["e4_pair_count"] = repeats
    summary["e3_confirmation_runs"] = 3
    summary["answer_screen_passes"] = sum(m["answer_screen_passes"] for m in metrics.values())
    summary["answer_screen_count"] = sum(m["answer_screen_count"] for m in metrics.values())
    summary["model_requests"] = sum(m["n_workers"] for m in metrics.values())
    for mark in ("zwsp_k", "short", "sentence"):
        summary[f"e3_{mark}_reusing_hit_rate"] = metrics[f"e3/{mark}"]["worker_2_to_n_hit_rate"]

    e4 = []
    for rep in range(1, repeats + 1):
        none = metrics[f"e1/r{rep}/none"]["workers"]
        front = metrics[f"e1/r{rep}/prefix"]["workers"]
        for left, right in zip(none, front):
            e4.append(dict(replicate=rep, worker=left["worker"],
                           none_prompt_tokens=left["prompt_tokens"],
                           front_prompt_tokens=right["prompt_tokens"],
                           delta_prompt_tokens=right["prompt_tokens"] - left["prompt_tokens"],
                           none_cached_tokens=left["cached_tokens"],
                           front_cached_tokens=right["cached_tokens"]))
    write_csv(session / "anchor-metrics-ci.csv", detailed)
    write_csv(session / "e4-paired-confirmation.csv", e4)
    write_csv(session / "cross-model-summary.csv", [summary])
    prior_audit = load(session / "report-audit.json", {})
    save(session / "report-audit.json", dict(run_ids=index, unique_salts=True,
         cold_worker_1=True, total_runs=expected, method="paired t CI",
         completed_utc=prior_audit.get("completed_utc") or
         datetime.now(timezone.utc).isoformat()))
    print("REPORT", session / "cross-model-summary.csv", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("init", "run", "report", "status"))
    parser.add_argument("--session", type=Path)
    parser.add_argument("--runs-root", type=Path, default=RUNS)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    root = args.runs_root.resolve()
    if args.phase == "init":
        if args.repeats < 5:
            parser.error("--repeats must be at least 5")
        print(new_session(root, args.repeats))
        return
    if args.session is None:
        parser.error("--session is required")
    session = args.session.resolve()
    if not session.is_dir() or not session.is_relative_to(root):
        parser.error("--session must be a directory under --runs-root")
    if args.phase == "run":
        asyncio.run(execute(session, root))
    elif args.phase == "report":
        report(session, root)
    else:
        index = load(session / "anchor-index.json", {})
        expected = load(session / "session.json")["repeats"] * 13 + 3
        print(f"{len(index)}/{expected} runs", session)
        for key, value in index.items():
            print(key, value)


if __name__ == "__main__":
    main()
