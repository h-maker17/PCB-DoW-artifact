"""Sequential, homogeneous AutoGen workers for cross-agent prefix-cache measurement.

The coordinator is a deterministic application scheduler. Every worker is a
real AutoGen AssistantAgent using the existing official-client Trace pipeline.
Only response telemetry from the live vLLM server is experimental evidence.
"""

import argparse
import asyncio
import csv
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
import httpx

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.messages import TextMessage
from autogen_core import CancellationToken
from autogen_core.model_context import UnboundedChatCompletionContext

from lab import BASE, Trace, inspect_prompts, tools_for, write_json


ASSET = Path(__file__).resolve().parent / "assets" / "shared_doc.txt"
SYSTEM = (
    "You are a research worker. Answer only your assigned question from the "
    "shared reference. Cite the relevant record numbers. If a calculation or "
    "workspace operation is needed, use the available tools. Keep the answer "
    "brief and factual."
)
TASKS = (
    "Which records describe planning and what common rule do they use?",
    "Which records describe verification and what common rule do they use?",
    "Which records describe storage and what common rule do they use?",
    "Which records describe scheduling and what common rule do they use?",
    "Which records describe measurement and what common rule do they use?",
)
FIELDS = ("prompt_tokens", "cached_tokens", "uncached_tokens",
          "completion_tokens", "total_tokens")
EXPECTED = (
    (range(1, 6), "planning", ("goal", "depend", "plan", "schedule", "task")),
    (range(6, 11), "verification", ("claim", "check", "review", "artifact", "verify")),
    (range(11, 16), "storage", ("file", "run", "record", "archiv", "manifest")),
    (range(16, 21), "scheduling", ("coordinator", "task", "order", "schedule", "worker")),
    (range(21, 26), "measurement", ("token", "cache", "metric", "measure", "telemetry")),
)


def sha256_text(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def shared_document(asset=ASSET, shared_tokens=2000):
    """Construct a deterministic candidate with this many words for calibration."""
    if shared_tokens < 128 or shared_tokens > 12000:
        raise ValueError("--shared-tokens must be in [128, 12000]")
    source = Path(asset).read_text(encoding="utf-8").strip()
    if not source:
        raise ValueError("Shared document is empty")
    # An English word count is only a size knob, never the measured Qwen count.
    words = source.split()
    repeats = (shared_tokens + len(words) - 1) // len(words)
    selected = (" ".join(words) + "\n") * repeats
    return " ".join(selected.split()[:shared_tokens])


async def calibrate_shared_document(asset, target_tokens, url, model, transport=None):
    """Use the live server tokenizer to target the document's incremental Qwen tokens."""
    if not 128 <= target_tokens <= 12000:
        raise ValueError("--shared-tokens must be in [128, 12000]")
    async with httpx.AsyncClient(base_url=url.rstrip("/"), timeout=120,
                                 transport=transport) as client:
        async def count(content):
            response = await client.post("/tokenize", json=dict(
                model=model, messages=[dict(role="user", content=content)],
                chat_template_kwargs=dict(enable_thinking=False)))
            response.raise_for_status()
            data = response.json()
            if type(data.get("count")) is not int:
                raise RuntimeError("Server /tokenize did not return a token count.")
            return data["count"]
        base = await count("")
        lower, upper = 128, min(12000, max(256, target_tokens * 2))
        best = None
        while lower <= upper:
            midpoint = (lower + upper) // 2
            candidate = shared_document(asset, midpoint)
            incremental = await count(candidate) - base
            if incremental <= target_tokens:
                best = (candidate, incremental)
                lower = midpoint + 1
            else:
                upper = midpoint - 1
        if best is None:
            raise RuntimeError("Even the shortest shared document exceeds the target token count.")
        return best


def marker(index, kind="zwsp_k"):
    if kind == "zwsp_k":
        return "\u200b" * index
    if kind == "short":
        return f"<!--w{index}-->"
    if kind == "sentence":
        return f"<!-- This reference copy has audit label {index}; ignore the label. -->"
    raise ValueError(kind)


def worker_prompt(document, task, index, variance, mark="zwsp_k", midpoint=None):
    tag = marker(index, mark)
    if variance == "none":
        prefix = document
        suffix = ""
    elif variance == "prefix":
        prefix = tag + "\n" + document
        suffix = ""
    elif variance == "midfix":
        words = document.split()
        cut = midpoint if midpoint is not None else len(words) // 2
        prefix = " ".join(words[:cut]) + "\n" + tag + "\n" + " ".join(words[cut:])
        suffix = ""
    elif variance == "suffix":
        prefix = document
        suffix = "\n" + tag
    else:
        raise ValueError(variance)
    return prefix + "\n\nYour subtask: " + task + suffix


def check_answer(answer, index):
    """Conservative, auditable citation-and-topic screen; not a semantic oracle."""
    expected, category, cues = EXPECTED[(index - 1) % len(EXPECTED)]
    content = answer.get("chat_message", {}).get("content", "")
    content = content if isinstance(content, str) else ""
    cited = {int(x) for x in re.findall(r"\b(?:Record\s*)?0?([1-9]|1[0-9]|2[0-5])\b", content, re.I)}
    for start, end in re.findall(
            r"\bRecords?\s+0?([1-9]|1[0-9]|2[0-5])\s*(?:to|through|[-–])\s*"
            r"0?([1-9]|1[0-9]|2[0-5])\b", content, re.I):
        if int(start) <= int(end) and int(end) - int(start) <= 24:
            cited.update(range(int(start), int(end) + 1))
    missing = sorted(set(expected) - cited)
    extra = sorted(cited - set(expected))
    topic = any(cue in content.lower() for cue in cues)
    explanatory = len(re.findall(r"\b\w+\b", content)) >= 20
    return dict(worker=index, category=category, expected_records=list(expected),
                cited_records=sorted(cited), missing_records=missing,
                out_of_category_records=extra, topic_cue_present=topic,
                explanatory_length=explanatory,
                screen_pass=not missing and not extra and topic and explanatory,
                answer=content,
                limitation="Automated citation/topic screen; manual semantic review remains necessary.")


async def midpoint_word_index(document, url, model, transport=None):
    """Find the word boundary nearest 50% of live tokenizer's document tokens."""
    words = document.split()
    async with httpx.AsyncClient(base_url=url.rstrip("/"), timeout=120,
                                 transport=transport) as client:
        async def count(content):
            response = await client.post("/tokenize", json=dict(
                model=model, messages=[dict(role="user", content=content)],
                chat_template_kwargs=dict(enable_thinking=False)))
            response.raise_for_status()
            return response.json()["count"]
        base = await count("")
        target = (await count(document) - base) / 2
        low, high = 1, len(words) - 1
        best = (1, float("inf"), 0)
        while low <= high:
            cut = (low + high) // 2
            actual = await count(" ".join(words[:cut])) - base
            if abs(actual - target) < best[1]:
                best = (cut, abs(actual - target), actual)
            if actual < target:
                low = cut + 1
            else:
                high = cut - 1
        return best[0], best[2], target * 2


def lcp(left, right):
    count = 0
    for a, b in zip(left, right):
        if a != b:
            break
        count += 1
    return count


def response_ids(dest, call):
    path = Path(dest) / f"{call}.response.json"
    if not path.is_file():
        return None
    ids = json.loads(path.read_text(encoding="utf-8")).get("prompt_token_ids")
    return ids if isinstance(ids, list) and all(type(n) is int for n in ids) else None


def analyze_run(dest, workers):
    """Compare actual server IDs and usage; unknown telemetry stays unknown."""
    dest = Path(dest)
    manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    rows = json.loads((dest / "usage.json").read_text(encoding="utf-8")) if (
        dest / "usage.json").exists() else []
    first = []
    result = []
    for index in range(1, workers + 1):
        agent = f"worker_{index}"
        agent_rows = [row for row in rows if row["agent"] == agent]
        row = agent_rows[0] if agent_rows else None
        ids = response_ids(dest, row["call"]) if row else None
        if ids is not None:
            first.append(ids)
        prior = first[0] if first else None
        common = lcp(prior, ids) if prior is not None and ids is not None else None
        prompt = row["prompt_tokens"] if row else None
        hit = row["cached_tokens"] if row else None
        result.append(dict(worker=index, agent=agent, requests=len(agent_rows),
                           first_call=row["call"] if row else None,
                           prompt_tokens=prompt, cached_tokens=hit,
                           uncached_tokens=row["uncached_tokens"] if row else None,
                           completion_tokens=row["completion_tokens"] if row else None,
                           total_tokens=row["total_tokens"] if row else None,
                           hit_rate=(hit / prompt) if type(hit) is int and
                           type(prompt) is int and prompt > 0 else None,
                           lcp_with_worker_1=common,
                           first_prompt_ids_sha256=sha256_text(
                               json.dumps(ids, separators=(",", ":"))) if ids is not None else None))

    # A high reported hit rate alone can be inherited from prior runs. The
    # unique cache_salt and worker 1 cold observation are both required.
    if not manifest["natural_model_run"]:
        status, reason = "fixture", "Mock transport validates structure only."
    elif len(result) < 2 or any(row["requests"] != 1 for row in result):
        status, reason = "inconclusive", "Expected exactly one model request per worker."
    elif any(row["hit_rate"] is None or row["lcp_with_worker_1"] is None for row in result):
        status, reason = "inconclusive", "Server token IDs or cache telemetry are missing."
    elif result[0]["cached_tokens"] != 0:
        status, reason = "inconclusive", "Worker 1 was not cold; check server and cache salt."
    elif all(row["hit_rate"] >= 0.70 and
             row["cached_tokens"] <= row["lcp_with_worker_1"]
             for row in result[1:]):
        status, reason = "go", "Workers 2..N share a long exact prefix and report high cache reuse."
    else:
        status, reason = "no-go", (
            "Cross-worker reuse below 70% or inconsistent with actual token IDs; "
            "inspect rendered requests, server instance, APC setting, and eviction.")
    summary = dict(status=status, reason=reason, threshold_hit_rate=0.70,
                   evidence="live vLLM response required; fixtures are not measurements",
                   workers=result)
    write_json(dest / "shared-prefix-summary.json", summary)
    columns = ("worker", "agent", "requests", "first_call", "prompt_tokens",
               "cached_tokens", "uncached_tokens", "completion_tokens",
               "total_tokens", "hit_rate", "lcp_with_worker_1")
    with (dest / "shared-prefix-table.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(result)
    valid_usage = all(type(row[k]) is int for row in result for k in
                      ("prompt_tokens", "cached_tokens", "uncached_tokens"))
    costs = {}
    if valid_usage:
        for fraction in (0.1, 0.25, 0.5):
            costs[str(fraction)] = sum(row["uncached_tokens"] +
                                        fraction * row["cached_tokens"] for row in result)
    checks_path = dest / "answer-checks.json"
    checks = json.loads(checks_path.read_text(encoding="utf-8")) if checks_path.exists() else []
    metrics = dict(run_id=dest.name, variance=manifest.get("variance"),
                   mark=manifest.get("mark"), n_workers=workers,
                   source="live vLLM" if manifest["natural_model_run"] else "fixture",
                   cold_worker_1=(result[0]["cached_tokens"] == 0) if result else None,
                   one_request_per_worker=all(row["requests"] == 1 for row in result),
                   prompt_tokens=sum(row["prompt_tokens"] for row in result) if valid_usage else None,
                   cached_tokens=sum(row["cached_tokens"] for row in result) if valid_usage else None,
                   uncached_tokens=sum(row["uncached_tokens"] for row in result) if valid_usage else None,
                   hit_rate=(sum(row["cached_tokens"] for row in result) /
                             sum(row["prompt_tokens"] for row in result)) if valid_usage else None,
                   worker_2_to_n_hit_rate=(sum(row["cached_tokens"] for row in result[1:]) /
                                           sum(row["prompt_tokens"] for row in result[1:]))
                   if valid_usage and workers > 1 else None,
                   cost_by_cache_fraction=costs,
                   answer_screen_passes=sum(c["screen_pass"] for c in checks),
                   answer_screen_count=len(checks), workers=result,
                   caf_iie=None,
                   caf_iie_note="IIE paper/formula not identified in supplied materials; not inferred.")
    write_json(dest / "attack-metrics.json", metrics)
    return summary


async def run(args, *, transport=None):
    if any(version(p) != "0.7.5" for p in
           ("autogen-agentchat", "autogen-core", "autogen-ext")):
        raise RuntimeError("This lab requires AutoGen 0.7.5.")
    workers = getattr(args, "n_workers", None) or args.workers
    mark = getattr(args, "mark", "zwsp_k")
    if not 2 <= workers <= 20:
        raise ValueError("--workers must be in [2, 20]")
    if args.variance not in ("none", "prefix", "midfix", "suffix"):
        raise ValueError(args.variance)
    source = Path(args.asset).read_bytes()
    document, calibrated_tokens = await calibrate_shared_document(
        args.asset, args.shared_tokens, args.url, args.model, transport)
    midpoint, midpoint_tokens, document_tokens = (None, None, None)
    if args.variance == "midfix":
        midpoint, midpoint_tokens, document_tokens = await midpoint_word_index(
            document, args.url, args.model, transport)
    run_id = (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") +
              "-mapshared-" + args.variance + "-" + uuid.uuid4().hex[:6])
    trace = Trace(Path(args.output) / run_id, args.url, args.model,
                  transport=transport, max_calls=workers)
    manifest = dict(mode="mapshared", variance=args.variance, mark=mark,
                    workers=workers, n_workers=workers,
                    restarted_server_before_run=False,
                    isolation="unique cache_salt per run; same salt within run",
                    injection_mechanism="controlled per-worker prompt assembly, not one identical static document",
                    midpoint_word_index=midpoint, midpoint_tokens=midpoint_tokens,
                    document_tokens_for_midpoint=document_tokens,
                    execution="sequential", coordinator="deterministic application scheduler",
                    worker_framework="AutoGen AssistantAgent 0.7.5",
                    system_sha256=sha256_text(SYSTEM), shared_asset_sha256=hashlib.sha256(source).hexdigest(),
                    shared_doc_sha256=sha256_text(document),
                    shared_tokens_target=args.shared_tokens,
                    shared_tokens_calibrated_no_tools=calibrated_tokens,
                    calibration="vLLM /tokenize; incremental user-content tokens without tools",
                    shared_words_actual=len(document.split()),
                    tokenizer_note="Full actual prompt IDs and cached tokens come from chat completion responses.",
                    tasks=[TASKS[(i - 1) % len(TASKS)] + f" (worker {i})"
                           for i in range(1, workers + 1)],
                    url=args.url, model=args.model, cache_salt=trace.salt,
                    natural_model_run=(transport is None), api=dict(temperature=0, seed=42,
                    enable_thinking=False, parallel_tool_calls=False),
                    versions={p: version(p) for p in
                              ("autogen-agentchat", "autogen-core", "autogen-ext", "openai", "httpx")})
    write_json(trace.dest / "manifest.json", manifest)
    (trace.dest / "shared_doc.used.txt").write_text(document, encoding="utf-8")
    shared_tools = tools_for(trace)
    checks = []
    try:
        for index in range(1, workers + 1):
            name = f"worker_{index}"
            task = manifest["tasks"][index - 1]
            agent = AssistantAgent(
                name, model_client=trace.client(name), system_message=SYSTEM,
                tools=shared_tools, model_context=UnboundedChatCompletionContext(),
                max_tool_iterations=1, reflect_on_tool_use=False)
            prompt = worker_prompt(document, task, index, args.variance, mark, midpoint)
            trace.event("worker_start", agent=name, prompt_sha256=sha256_text(prompt))
            answer = await agent.on_messages(
                [TextMessage(content=prompt, source="coordinator")],
                CancellationToken())
            write_json(trace.dest / f"{name}.result.json", answer)
            checks.append(check_answer(json.loads((trace.dest / f"{name}.result.json")
                                             .read_text(encoding="utf-8")), index))
            write_json(trace.dest / "answer-checks.json", checks)
            trace.event("worker_end", agent=name)
    except BaseException as exc:
        trace.event("run_error", error=repr(exc))
        raise
    finally:
        try:
            await inspect_prompts(trace)
        finally:
            await trace.close()
            analyze_run(trace.dest, workers)
    return trace.dest


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--n-workers", type=int, default=None)
    parser.add_argument("--shared-tokens", type=int, default=2000,
                        help="Approximate English word count; server IDs are authoritative.")
    parser.add_argument("--asset", default=str(ASSET))
    parser.add_argument("--variance", choices=["none", "prefix", "midfix", "suffix"], default="none")
    parser.add_argument("--mark", choices=["zwsp_k", "short", "sentence"], default="zwsp_k")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--model", default="qwen3-8b")
    parser.add_argument("--output", default=str(
        BASE / "多智能体系统实验数据" / "autogen-handoff-runs"))
    return parser.parse_args(argv)


if __name__ == "__main__":
    print(asyncio.run(run(parse_args())))
