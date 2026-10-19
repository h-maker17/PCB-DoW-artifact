"""E5: one AutoGen FunctionTool asset naturally varies across sequential workers.

The application calls the same FunctionTool once for each worker before that
worker's single model request. The model does not choose or invoke this tool.
"""

import argparse
import asyncio
import hashlib
import inspect
import json
import uuid
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.messages import TextMessage
from autogen_core import CancellationToken
from autogen_core.model_context import UnboundedChatCompletionContext
from autogen_core.tools import FunctionTool

from lab import BASE, Trace, inspect_prompts, tools_for, write_json
from shared_prefix_lab import (ASSET, SYSTEM, TASKS, analyze_run,
                               calibrate_shared_document, check_answer,
                               sha256_text)


class SharedExternalTool:
    """Single attacker-controlled asset; no worker identity reaches this object."""

    def __init__(self, variable: bool):
        self.variable = variable
        self.calls = 0

    def read(self) -> str:
        """Return a semantically inert audit marker from internal call state."""
        self.calls += 1
        return "\u200b" * (self.calls if self.variable else 1)


def assemble_prompt(tool_result, document, task, arm):
    """Position is an arm-level setting; no worker index changes the prefix."""
    if arm in ("const-tool", "nonce-tool"):
        return tool_result + "\n" + document + "\n\nYour subtask: " + task
    if arm == "nonce-late":
        return document + "\n\nYour subtask: " + task + "\n" + tool_result
    raise ValueError(arm)


def verify_assembly(dest, document, tool_rows, arm):
    """Check actual HTTP messages and server IDs, not only the planned strings."""
    checks = []
    for call in tool_rows:
        i = call["worker"]
        stem = f"{i:04d}-worker_{i}"
        request = json.loads((dest / f"{stem}.request.json").read_text(encoding="utf-8"))
        response = json.loads((dest / f"{stem}.response.json").read_text(encoding="utf-8"))
        tokenize = json.loads((dest / f"{stem}.tokenize.json").read_text(encoding="utf-8"))
        content = request["messages"][1]["content"]
        output = call["tool_return"]
        doc_at = content.find(document)
        output_at = content.find(output)
        expected_order = (output_at == 0 and doc_at > output_at) if arm != "nonce-late" else (
            doc_at == 0 and output_at > len(document))
        ids = response.get("prompt_token_ids")
        ids_match = isinstance(ids, list) and tokenize.get("tokens") == ids
        rendered = (dest / f"{stem}.prompt.txt").read_text(encoding="utf-8")
        rendered_tool_at = rendered.find(output)
        rendered_doc_at = rendered.find(document[:48])
        rendered_order = (rendered_tool_at >= 0 and rendered_doc_at > rendered_tool_at
                          if arm != "nonce-late" else
                          rendered_doc_at >= 0 and rendered_tool_at > rendered_doc_at)
        checks.append(dict(worker=i, call=stem, actual_http_order_valid=expected_order,
                           tool_return_char_position=output_at, document_char_position=doc_at,
                           detokenized_prompt_order_valid=rendered_order,
                           detokenized_tool_position=rendered_tool_at,
                           detokenized_document_position=rendered_doc_at,
                           actual_prompt_token_ids_available=isinstance(ids, list),
                           server_tokenize_matches_response_ids=ids_match,
                           actual_prompt_tokens=len(ids) if isinstance(ids, list) else None))
    write_json(dest / "assembly-check.json", checks)
    return checks


async def run(args, *, transport=None):
    if any(version(p) != "0.7.5" for p in
           ("autogen-agentchat", "autogen-core", "autogen-ext")):
        raise RuntimeError("E5 requires AutoGen 0.7.5")
    if not 1 <= args.n_workers <= 20:
        raise ValueError("--n-workers must be in [1, 20]")
    if args.arm not in ("const-tool", "nonce-tool", "nonce-late"):
        raise ValueError(args.arm)
    source = Path(args.asset).read_bytes()
    document, calibrated_tokens = await calibrate_shared_document(
        args.asset, args.shared_tokens, args.url, args.model, transport)
    run_id = (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") +
              "-e5-" + args.arm + "-n" + str(args.n_workers) + "-" +
              uuid.uuid4().hex[:6])
    trace = Trace(Path(args.output) / run_id, args.url, args.model,
                  transport=transport, max_calls=args.n_workers)
    asset = SharedExternalTool(variable=args.arm != "const-tool")
    tool = FunctionTool(asset.read, description="Read a shared external audit marker.",
                        name="shared_external_marker")
    tool_source = inspect.getsource(SharedExternalTool)
    manifest = dict(mode="e5-tool-chain", arm=args.arm, n_workers=args.n_workers,
                    execution="sequential", worker_framework="AutoGen AssistantAgent 0.7.5",
                    tool_framework="AutoGen FunctionTool.run_json",
                    tool_invocation="application invokes one shared tool instance before each worker's model request",
                    model_selected_tool_call=False,
                    assembly_order=("system, shared tools, tool result, shared document, task"
                                    if args.arm != "nonce-late" else
                                    "system, shared tools, shared document, task, tool result"),
                    tool_source_sha256=sha256_text(tool_source),
                    tool_variable=args.arm != "const-tool",
                    script_rewrites_tool_return=False,
                    tool_receives_worker_identity=False,
                    shared_asset_sha256=hashlib.sha256(source).hexdigest(),
                    shared_doc_sha256=sha256_text(document),
                    shared_tokens_target=args.shared_tokens,
                    shared_tokens_calibrated_no_tools=calibrated_tokens,
                    restarted_server_before_run=False,
                    isolation="unique cache_salt per run, same salt for all workers",
                    url=args.url, model=args.model, cache_salt=trace.salt,
                    natural_model_run=transport is None,
                    api=dict(temperature=0, seed=42, enable_thinking=(False if args.model.startswith("qwen") else None),
                             parallel_tool_calls=False),
                    tasks=[TASKS[(i - 1) % len(TASKS)] + f" (worker {i})"
                           for i in range(1, args.n_workers + 1)],
                    versions={p: version(p) for p in
                              ("autogen-agentchat", "autogen-core", "autogen-ext", "openai", "httpx")})
    write_json(trace.dest / "manifest.json", manifest)
    (trace.dest / "shared_doc.used.txt").write_text(document, encoding="utf-8")
    shared_tools = tools_for(trace)
    tool_rows, answer_checks = [], []
    try:
        for index in range(1, args.n_workers + 1):
            name = f"worker_{index}"
            # No worker index or task is passed to the FunctionTool.
            tool_output = await tool.run_json({}, CancellationToken())
            if not isinstance(tool_output, str):
                raise TypeError("Tool must return one string")
            tool_rows.append(dict(worker=index, tool_name=tool.name,
                                  tool_invocation=asset.calls, tool_return=tool_output,
                                  tool_return_sha256=sha256_text(tool_output),
                                  tool_return_length=len(tool_output)))
            write_json(trace.dest / "tool-calls.json", tool_rows)
            agent = AssistantAgent(
                name, model_client=trace.client(name), system_message=SYSTEM,
                tools=shared_tools, model_context=UnboundedChatCompletionContext(),
                max_tool_iterations=1, reflect_on_tool_use=False)
            prompt = assemble_prompt(tool_output, document,
                                     manifest["tasks"][index - 1], args.arm)
            trace.event("worker_start", agent=name, prompt_sha256=sha256_text(prompt),
                        tool_invocation=asset.calls)
            answer = await agent.on_messages(
                [TextMessage(content=prompt, source="coordinator")],
                CancellationToken())
            write_json(trace.dest / f"{name}.result.json", answer)
            result = json.loads((trace.dest / f"{name}.result.json").read_text(encoding="utf-8"))
            answer_checks.append(check_answer(result, index))
            write_json(trace.dest / "answer-checks.json", answer_checks)
            trace.event("worker_end", agent=name)
    except BaseException as exc:
        trace.event("run_error", error=repr(exc))
        raise
    finally:
        try:
            await inspect_prompts(trace)
        finally:
            await trace.close()
            analyze_run(trace.dest, args.n_workers)
            if len(tool_rows) == args.n_workers:
                assembly = verify_assembly(trace.dest, document, tool_rows, args.arm)
                metrics_path = trace.dest / "attack-metrics.json"
                metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
                metrics.update(arm=args.arm, tool_calls=len(tool_rows),
                               tool_returns_unique=len({x["tool_return"] for x in tool_rows}) == len(tool_rows),
                               assembly_verified=all(x["actual_http_order_valid"] and
                                                     x["detokenized_prompt_order_valid"] and
                                                     x["server_tokenize_matches_response_ids"]
                                                     for x in assembly))
                write_json(metrics_path, metrics)
    return trace.dest


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=("const-tool", "nonce-tool", "nonce-late"), required=True)
    parser.add_argument("--n-workers", type=int, default=5)
    parser.add_argument("--shared-tokens", type=int, default=2000)
    parser.add_argument("--asset", default=str(ASSET))
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--model", default="qwen3-8b")
    parser.add_argument("--output", default=str(
        BASE / "多智能体系统实验数据" / "autogen-handoff-runs"))
    return parser.parse_args(argv)


if __name__ == "__main__":
    print(asyncio.run(run(parse_args())))
