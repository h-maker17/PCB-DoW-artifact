"""Run prior Part B and E5 experiments in small, resumable manual phases.

Each phase writes run IDs immediately. Reports use these new IDs rather than
the original 2026-10-02 experiment index. Part A has its own existing runner.
"""
import argparse
import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from lab import BASE
from shared_prefix_lab import ASSET, run as run_part_b
from e5_tool_chain import run as run_e5

RUNS = BASE / "runs"
PHASES = ("init", "e1", "e2", "e3", "e5-probe", "e5-n5", "e5-n10", "e5-n20")


def read_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def validate_session(session, runs_root):
    session = Path(session).resolve()
    root = Path(runs_root).resolve()
    if not session.is_dir() or not session.is_relative_to(root):
        raise ValueError("--session must name a session directory under --runs-root")
    return session


def part_b_args(runs_root, n, variance, mark="zwsp_k"):
    return SimpleNamespace(output=str(runs_root), url="http://127.0.0.1:8000",
                           model="qwen3-8b", n_workers=n, workers=n,
                           asset=str(ASSET), shared_tokens=2000,
                           variance=variance, mark=mark)


def e5_args(runs_root, n, arm):
    return SimpleNamespace(output=str(runs_root), url="http://127.0.0.1:8000",
                           model="qwen3-8b", n_workers=n, asset=str(ASSET),
                           shared_tokens=2000, arm=arm)


def validated_metrics(dest, n):
    metrics = read_json(dest / "attack-metrics.json", {})
    if not (metrics.get("cold_worker_1") is True and
            metrics.get("one_request_per_worker") is True and
            metrics.get("answer_screen_count") == n):
        raise RuntimeError(f"Run needs inspection before continuing: {dest}")
    return metrics


async def execute(args):
    root = Path(args.runs_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if args.phase == "init":
        session = root / ("manual-replay-" +
                          datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") +
                          "-" + uuid.uuid4().hex[:6])
        session.mkdir()
        save_json(session / "session.json", dict(created_utc=datetime.now(timezone.utc).isoformat(),
                                                  runs_root=str(root), model="qwen3-8b",
                                                  execution="sequential"))
        print(session)
        return
    session = validate_session(args.session, root)
    b_path = session / "part-b-index.json"
    e_path = session / "e5-runs-index.json"
    b_index = read_json(b_path, {"E1": {}, "E2": {}, "E3": {}})
    e_index = read_json(e_path, {})

    async def b_one(group, key, n, variance, mark="zwsp_k"):
        section = b_index[group]
        if key in section:
            print("SKIP existing", group, key, section[key])
            return
        dest = await run_part_b(part_b_args(root, n, variance, mark))
        metrics = validated_metrics(dest, n)
        section[key] = dest.name
        save_json(b_path, b_index)
        print("SAVED", group, key, dest.name,
              "hit_2_to_n=", metrics.get("worker_2_to_n_hit_rate"),
              "answers=", metrics.get("answer_screen_passes"), "/", n)

    async def e_one(n, arm):
        section = e_index.setdefault(str(n), {})
        if arm in section:
            print("SKIP existing E5", n, arm, section[arm])
            return
        dest = await run_e5(e5_args(root, n, arm))
        metrics = validated_metrics(dest, n)
        if not metrics.get("assembly_verified"):
            raise RuntimeError(f"Actual model input order not verified: {dest}")
        section[arm] = dest.name
        save_json(e_path, e_index)
        print("SAVED E5", n, arm, dest.name,
              "hit_2_to_n=", metrics.get("worker_2_to_n_hit_rate"),
              "answers=", metrics.get("answer_screen_passes"), "/", n)

    if args.phase == "e1":
        for variance in ("none", "prefix", "midfix", "suffix"):
            await b_one("E1", variance, 5, variance)
    elif args.phase == "e2":
        for n in (3, 5, 10, 20):
            for variance in ("none", "prefix"):
                section = b_index["E2"].setdefault(str(n), {})
                if variance in section:
                    print("SKIP existing E2", n, variance, section[variance])
                    continue
                dest = await run_part_b(part_b_args(root, n, variance))
                metrics = validated_metrics(dest, n)
                section[variance] = dest.name
                save_json(b_path, b_index)
                print("SAVED E2", n, variance, dest.name,
                      "hit_2_to_n=", metrics.get("worker_2_to_n_hit_rate"),
                      "answers=", metrics.get("answer_screen_passes"), "/", n)
    elif args.phase == "e3":
        for mark in ("zwsp_k", "short", "sentence"):
            await b_one("E3", mark, 5, "prefix", mark)
    elif args.phase == "e5-probe":
        probe_file = session / "e5-probe-run.txt"
        if probe_file.exists():
            print("SKIP existing E5 probe", probe_file.read_text(encoding="utf-8").strip())
        else:
            dest = await run_e5(e5_args(root, 1, "nonce-tool"))
            metrics = validated_metrics(dest, 1)
            if not metrics.get("assembly_verified"):
                raise RuntimeError(f"Probe actual input order not verified: {dest}")
            probe_file.write_text(dest.name + "\n", encoding="utf-8")
            print("SAVED E5 probe", dest.name, "assembly_verified=True")
    elif args.phase.startswith("e5-n"):
        n = int(args.phase.removeprefix("e5-n"))
        for arm in ("const-tool", "nonce-tool", "nonce-late"):
            await e_one(n, arm)
    print("SESSION=", session)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=PHASES)
    parser.add_argument("--session", type=Path)
    parser.add_argument("--runs-root", type=Path, default=RUNS)
    arguments = parser.parse_args()
    if arguments.phase != "init" and arguments.session is None:
        parser.error("--session is required outside init")
    asyncio.run(execute(arguments))
