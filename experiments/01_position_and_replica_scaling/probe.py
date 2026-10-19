"""Controlled HTTP replay of an observed native AutoGen request; no tools are executed."""
import argparse
import asyncio
import copy
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
import httpx
from lab import usage_of, write_json


def lcp(left, right):
    for i, (a, b) in enumerate(zip(left, right)):
        if a != b:
            return i
    return min(len(left), len(right))


def mutate(request, variant):
    body = copy.deepcopy(request)
    if variant == "system":
        message = next(m for m in body["messages"] if m["role"] == "system")
        message["content"] = "VARIANT_SYSTEM: " + message["content"]
    elif variant == "handoff-text":
        candidates = [m for m in body["messages"] if m.get("role") == "user"
                      and isinstance(m.get("content"), str) and "HANDOFF_" in m["content"]]
        if not candidates:
            raise ValueError("Choose a request following a handoff, e.g. the first worker request.")
        candidates[-1]["content"] += " VARIANT_HANDOFF: independently verify every result."
    elif variant == "tool-description":
        body["tools"][0]["function"]["description"] += " VARIANT_TOOL: inspect carefully."
    return body


async def run(args):
    src = Path(args.run)
    request_file = src / (args.call + ".request.json")
    request = json.loads(request_file.read_text(encoding="utf-8"))
    out = src / ("probe-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6])
    out.mkdir()
    write_json(out / "manifest.json", dict(
        evidence_kind="controlled replay/ablation of an observed AutoGen wire request; no agent/tool execution",
        original_request=str(request_file), max_tokens=16, seed=42,
        variants=["handoff-text", "system", "tool-description"]))
    summaries = []
    async with httpx.AsyncClient(base_url=args.url.rstrip("/"), timeout=300) as client:
        for variant in ["handoff-text", "system", "tool-description"]:
            baseline = copy.deepcopy(request)
            baseline.update(cache_salt=uuid.uuid4().hex, max_tokens=16, temperature=0,
                            seed=42, stream=False, return_token_ids=True)
            cases = [("cold", baseline), ("warm", copy.deepcopy(baseline)),
                     ("changed", mutate(baseline, variant))]
            ids, rows = {}, []
            for label, body in cases:
                name = variant + "-" + label
                write_json(out / f"{name}.request.json", body)
                response = await client.post("/v1/chat/completions", json=body)
                write_json(out / f"{name}.response.json", response.json())
                response.raise_for_status()
                data = response.json()
                if not isinstance(data.get("prompt_token_ids"), list):
                    raise RuntimeError("Server omitted actual prompt IDs; cannot claim token prefix measurements.")
                ids[label] = data["prompt_token_ids"]
                row = dict(variant=variant, phase=label, **usage_of(data))
                rows.append(row)
                d = await client.post("/detokenize", json=dict(model=body["model"], tokens=ids[label]))
                d.raise_for_status()
                (out / f"{name}.prompt.txt").write_text(d.json()["prompt"], encoding="utf-8")
                print(json.dumps(row, ensure_ascii=False))
            prefix = lcp(ids["cold"], ids["changed"])
            summaries.append(dict(
                variant=variant, measurements=rows,
                baseline_repeat_token_ids_identical=(ids["cold"] == ids["warm"]),
                actual_lcp_tokens=prefix,
                first_divergence_token_index=(None if ids["cold"] == ids["changed"] else prefix),
                baseline_window=ids["cold"][max(0, prefix - 8):prefix + 8],
                changed_window=ids["changed"][max(0, prefix - 8):prefix + 8],
                note="LCP is a token sequence property, not a measured cache hit count; consult measurements."))
            write_json(out / "comparison.json", summaries)
    print("Comparison:", out)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--call", required=True, help="e.g. 0002-worker, from the run's request filenames")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    asyncio.run(run(parser.parse_args()))
