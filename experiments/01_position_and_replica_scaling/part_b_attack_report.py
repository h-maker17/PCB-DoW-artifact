import argparse
import csv
import json
from pathlib import Path

from shared_prefix_lab import analyze_run, check_answer

INDEX = {
    "E1": {
        "none": "20261002T080845Z-mapshared-none-56a0d2",
        "prefix": "20261002T080853Z-mapshared-prefix-bfb78a",
        "midfix": "20261002T080902Z-mapshared-midfix-eaf811",
        "suffix": "20261002T080910Z-mapshared-suffix-8c6f71",
    },
    "E2": {
        "3": {"none": "20261002T081153Z-mapshared-none-0c815e",
              "prefix": "20261002T081200Z-mapshared-prefix-3e8b45"},
        "5": {"none": "20261002T081207Z-mapshared-none-e23f8b",
              "prefix": "20261002T081215Z-mapshared-prefix-83c9f5"},
        "10": {"none": "20261002T081224Z-mapshared-none-cebbd9",
               "prefix": "20261002T081237Z-mapshared-prefix-fca91b"},
        "20": {"none": "20261002T081249Z-mapshared-none-5f9b3f",
               "prefix": "20261002T081308Z-mapshared-prefix-38db05"},
    },
    "E3": {
        "zwsp_k": "20261002T081357Z-mapshared-prefix-f09bef",
        "short": "20261002T081405Z-mapshared-prefix-154653",
        "sentence": "20261002T081414Z-mapshared-prefix-d582f5",
    },
}


def save_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main(runs_root, output, index=None):
    index = INDEX if index is None else index
    output.mkdir(parents=True, exist_ok=True)
    loaded, salts, audit = {}, set(), []
    named = [("E1", key, rid) for key, rid in index["E1"].items()]
    named += [("E2", f"{n}-{v}", rid) for n, pair in index["E2"].items()
              for v, rid in pair.items()]
    named += [("E3", key, rid) for key, rid in index["E3"].items()]
    for group, key, rid in named:
        run = runs_root / rid
        manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["natural_model_run"] and manifest["cache_salt"] not in salts
        assert manifest["restarted_server_before_run"] is False
        salts.add(manifest["cache_salt"])
        n = manifest["n_workers"]
        checks = []
        for i in range(1, n + 1):
            answer = json.loads((run / f"worker_{i}.result.json").read_text(encoding="utf-8"))
            checks.append(check_answer(answer, i))
            call = f"{i:04d}-worker_{i}"
            assert (run / f"{call}.request.json").is_file()
            assert (run / f"{call}.response.json").is_file()
        old = run / "answer-checks.json"
        original = run / "answer-checks.initial.json"
        if old.exists() and not original.exists():
            original.write_bytes(old.read_bytes())
        old.write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")
        analyze_run(run, n)
        metrics = json.loads((run / "attack-metrics.json").read_text(encoding="utf-8"))
        assert metrics["cold_worker_1"] is True
        assert metrics["one_request_per_worker"] is True
        assert metrics["answer_screen_count"] == n
        assert all(w["prompt_tokens"] == w["cached_tokens"] + w["uncached_tokens"]
                   for w in metrics["workers"])
        loaded[rid] = metrics
        audit.append(dict(group=group, condition=key, run_id=rid, n_workers=n,
                          variance=manifest["variance"], mark=manifest["mark"],
                          cold_worker_1=True, unique_salt=True,
                          requests=n, answer_passes=metrics["answer_screen_passes"],
                          answer_count=n, total_prompt=metrics["prompt_tokens"],
                          total_cached=metrics["cached_tokens"],
                          total_uncached=metrics["uncached_tokens"],
                          hit_rate_workers_2_to_n=metrics["worker_2_to_n_hit_rate"]))

    # A ratio requires a named, independent benign run. Preserve that pairing
    # in every run's derived metrics rather than implying an unpaired ratio.
    pairs = {}
    e1_benign = index["E1"]["none"]
    for rid in index["E1"].values():
        pairs[rid] = e1_benign
    for pair in index["E2"].values():
        for rid in pair.values():
            pairs[rid] = pair["none"]
    for rid in index["E3"].values():
        pairs[rid] = e1_benign
    for rid, benign_id in pairs.items():
        metric = loaded[rid]
        baseline = loaded[benign_id]
        metric["benign_pair_run_id"] = benign_id
        metric["caf_cache_by_fraction"] = {
            str(f): metric["cost_by_cache_fraction"][str(f)] /
            baseline["cost_by_cache_fraction"][str(f)]
            for f in (0.1, 0.25, 0.5)
        }
        (runs_root / rid / "attack-metrics.json").write_text(
            json.dumps(metric, ensure_ascii=False, indent=2), encoding="utf-8")

    e1 = [dict(variance=v, run_id=rid,
               worker_2_to_n_hit_rate=loaded[rid]["worker_2_to_n_hit_rate"],
               all_worker_hit_rate=loaded[rid]["hit_rate"],
               uncached_tokens=loaded[rid]["uncached_tokens"],
               answer_passes=loaded[rid]["answer_screen_passes"])
          for v, rid in index["E1"].items()]
    e2 = []
    for n, pair in index["E2"].items():
        benign, attack = (loaded[pair[k]] for k in ("none", "prefix"))
        row = dict(n_workers=int(n), benign_run=pair["none"], attack_run=pair["prefix"],
                   benign_uncached_tokens=benign["uncached_tokens"],
                   attack_uncached_tokens=attack["uncached_tokens"],
                   uncached_ratio=attack["uncached_tokens"] / benign["uncached_tokens"])
        for f in (0.1, 0.25, 0.5):
            label = str(f).replace(".", "_")
            b = benign["cost_by_cache_fraction"][str(f)]
            a = attack["cost_by_cache_fraction"][str(f)]
            row[f"benign_cost_f_{label}"] = b
            row[f"attack_cost_f_{label}"] = a
            row[f"caf_cache_f_{label}"] = a / b
        e2.append(row)
    e3 = [dict(mark=m, run_id=rid,
               worker_2_to_n_hit_rate=loaded[rid]["worker_2_to_n_hit_rate"],
               all_worker_hit_rate=loaded[rid]["hit_rate"],
               answer_passes=loaded[rid]["answer_screen_passes"])
          for m, rid in index["E3"].items()]
    none, prefix = (loaded[index["E1"][v]] for v in ("none", "prefix"))
    e4 = []
    for a, b in zip(none["workers"], prefix["workers"]):
        assert a["worker"] == b["worker"]
        e4.append(dict(worker=a["worker"], none_prompt_tokens=a["prompt_tokens"],
                       prefix_prompt_tokens=b["prompt_tokens"],
                       delta_prompt_tokens=b["prompt_tokens"] - a["prompt_tokens"],
                       none_cached_tokens=a["cached_tokens"],
                       prefix_cached_tokens=b["cached_tokens"],
                       none_uncached_tokens=a["uncached_tokens"],
                       prefix_uncached_tokens=b["uncached_tokens"]))
    for name, rows in (("audit.csv", audit), ("fig-a.csv", e1),
                       ("fig-b.csv", e2), ("fig-d.csv", e3), ("table-e4.csv", e4)):
        save_csv(output / name, rows)
    (output / "experiment-index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(dict(runs=len(audit), e1=e1, e2=e2, e3=e3, e4=e4), indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--index", type=Path,
                        help="JSON run index from a new manual replay; default is the original study")
    args = parser.parse_args()
    index = json.loads(args.index.read_text(encoding="utf-8")) if args.index else None
    main(args.runs_root, args.output, index)
