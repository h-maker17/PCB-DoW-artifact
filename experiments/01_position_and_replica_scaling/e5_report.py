"""Audit real E5 traces and compare one shared tool under three placements."""
import argparse
import csv
import json
from pathlib import Path

from e5_tool_chain import verify_assembly
from shared_prefix_lab import analyze_run, check_answer

INDEX = {
    5: {
        "const-tool": "20261002T085010Z-e5-const-tool-n5-08f055",
        "nonce-tool": "20261002T085019Z-e5-nonce-tool-n5-6368a3",
        "nonce-late": "20261002T085028Z-e5-nonce-late-n5-6cd4b9",
    },
    10: {
        "const-tool": "20261002T085059Z-e5-const-tool-n10-a07e64",
        "nonce-tool": "20261002T085111Z-e5-nonce-tool-n10-a95cc6",
        "nonce-late": "20261002T085124Z-e5-nonce-late-n10-d0d29b",
    },
    20: {
        "const-tool": "20261002T085138Z-e5-const-tool-n20-d83051",
        "nonce-tool": "20261002T085157Z-e5-nonce-tool-n20-6db62c",
        "nonce-late": "20261002T085217Z-e5-nonce-late-n20-f9bf3b",
    },
}
PROBE = "20261002T084901Z-e5-nonce-tool-n1-0857f2"


def save_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main(runs_root, output, index=None, probe=None, e1_prefix_run=None):
    index = INDEX if index is None else {int(k): v for k, v in index.items()}
    probe = PROBE if probe is None else probe
    output.mkdir(parents=True, exist_ok=True)
    audit, comparison = [], []
    salts, source_hashes = set(), set()
    all_answers = []
    for n, arms in sorted(index.items()):
        by_arm = {}
        for arm, rid in arms.items():
            run = runs_root / rid
            manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
            assert manifest["natural_model_run"] and manifest["n_workers"] == n
            assert manifest["arm"] == arm and not manifest["script_rewrites_tool_return"]
            assert not manifest["tool_receives_worker_identity"]
            assert manifest["cache_salt"] not in salts
            salts.add(manifest["cache_salt"])
            source_hashes.add(manifest["tool_source_sha256"])
            tool_calls = json.loads((run / "tool-calls.json").read_text(encoding="utf-8"))
            assert len(tool_calls) == n
            assert [x["tool_invocation"] for x in tool_calls] == list(range(1, n + 1))
            assert all(x["tool_name"] == "shared_external_marker" for x in tool_calls)
            outputs = [x["tool_return"] for x in tool_calls]
            if arm == "const-tool":
                assert len(set(outputs)) == 1 and all(len(x) == 1 for x in outputs)
            else:
                assert len(set(outputs)) == n
                assert [len(x) for x in outputs] == list(range(1, n + 1))
            doc = (run / "shared_doc.used.txt").read_text(encoding="utf-8")
            assembly = verify_assembly(run, doc, tool_calls, arm)
            assert all(x["actual_http_order_valid"] and
                       x["detokenized_prompt_order_valid"] and
                       x["server_tokenize_matches_response_ids"] for x in assembly)
            checks = []
            for i in range(1, n + 1):
                stem = f"{i:04d}-worker_{i}"
                assert (run / f"{stem}.request.json").is_file()
                assert (run / f"{stem}.response.json").is_file()
                answer = json.loads((run / f"worker_{i}.result.json").read_text(encoding="utf-8"))
                check = check_answer(answer, i)
                checks.append(check)
                all_answers.append(check)
            (run / "answer-checks.json").write_text(
                json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")
            analyze_run(run, n)
            metrics_path = run / "attack-metrics.json"
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            assert metrics["cold_worker_1"] and metrics["one_request_per_worker"]
            assert metrics["answer_screen_count"] == n
            assert all(w["prompt_tokens"] == w["cached_tokens"] + w["uncached_tokens"]
                       for w in metrics["workers"])
            metrics.update(arm=arm, tool_calls=n,
                           tool_returns_unique=len(set(outputs)) == n,
                           assembly_verified=True)
            by_arm[arm] = (rid, metrics)
            audit.append(dict(n_workers=n, arm=arm, run_id=rid,
                              unique_salt=True, cold_worker_1=True,
                              tool_calls=n, model_requests=n,
                              tool_returns_unique=len(set(outputs)) == n,
                              assembly_verified=True,
                              answer_passes=metrics["answer_screen_passes"],
                              answer_count=n,
                              worker_2_to_n_hit_rate=metrics["worker_2_to_n_hit_rate"],
                              prompt_tokens=metrics["prompt_tokens"],
                              cached_tokens=metrics["cached_tokens"],
                              uncached_tokens=metrics["uncached_tokens"],
                              lcp_worker_2=metrics["workers"][1]["lcp_with_worker_1"]))
        benign_id, benign = by_arm["const-tool"]
        for arm, (rid, metrics) in by_arm.items():
            costs = {str(f): metrics["cost_by_cache_fraction"][str(f)] /
                     benign["cost_by_cache_fraction"][str(f)] for f in (0.1, 0.25, 0.5)}
            metrics["benign_pair_run_id"] = benign_id
            metrics["caf_cache_by_fraction"] = costs
            (runs_root / rid / "attack-metrics.json").write_text(
                json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
            comparison.append(dict(n_workers=n, arm=arm, run_id=rid,
                                   benign_pair_run_id=benign_id,
                                   worker_2_to_n_hit_rate=metrics["worker_2_to_n_hit_rate"],
                                   uncached_tokens=metrics["uncached_tokens"],
                                   uncached_vs_const=metrics["uncached_tokens"] /
                                   benign["uncached_tokens"],
                                   caf_cache_f_0_1=costs["0.1"],
                                   caf_cache_f_0_25=costs["0.25"],
                                   caf_cache_f_0_5=costs["0.5"],
                                   answer_passes=metrics["answer_screen_passes"],
                                   answer_count=n))
    assert len(source_hashes) == 1
    save_csv(output / "e5-run-audit.csv", audit)
    save_csv(output / "e5-comparison.csv", comparison)
    (output / "e5-index.json").write_text(
        json.dumps(dict(probe=probe, runs=index), ensure_ascii=False, indent=2),
        encoding="utf-8")
    unique_answers = len({(x["category"], x["answer"]) for x in all_answers})
    e1_id = e1_prefix_run or "20261002T080853Z-mapshared-prefix-bfb78a"
    e1_metrics = json.loads((runs_root / e1_id / "attack-metrics.json").read_text(encoding="utf-8"))
    summary = dict(n_runs=len(audit), model_requests=sum(x["model_requests"] for x in audit),
                   tool_calls=sum(x["tool_calls"] for x in audit),
                   answer_passes=sum(x["answer_passes"] for x in audit),
                   answer_count=len(all_answers), unique_answer_variants=unique_answers,
                   tool_source_sha256=next(iter(source_hashes)),
                   probe_run_id=probe,
                   e1_prefix_n5_run_id=e1_id,
                   e1_prefix_n5_hit_rate=e1_metrics["worker_2_to_n_hit_rate"])
    summary["e5_nonce_n5_hit_rate"] = next(
        x["worker_2_to_n_hit_rate"] for x in audit
        if x["n_workers"] == 5 and x["arm"] == "nonce-tool")
    (output / "e5-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(dict(summary=summary, comparison=comparison), indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--index", type=Path,
                        help="JSON E5 arm mapping for a new manual replay")
    parser.add_argument("--probe-run", help="New one-worker assembly probe run ID")
    parser.add_argument("--probe-file", type=Path,
                        help="Read the new one-worker probe ID from a file")
    parser.add_argument("--e1-prefix-run", help="Matching new E1 prefix run ID")
    parser.add_argument("--part-b-index", type=Path,
                        help="Read matching E1 prefix ID from a new Part B index")
    args = parser.parse_args()
    index = json.loads(args.index.read_text(encoding="utf-8")) if args.index else None
    indexed_probe = None
    if index and "runs" in index:
        indexed_probe = index.get("probe")
        index = index["runs"]
    e1_run = args.e1_prefix_run
    probe_run = (args.probe_file.read_text(encoding="utf-8").strip() if args.probe_file
                 else args.probe_run or indexed_probe)
    if args.part_b_index:
        e1_run = json.loads(args.part_b_index.read_text(encoding="utf-8"))["E1"]["prefix"]
    main(args.runs_root, args.output, index, probe_run, e1_run)
