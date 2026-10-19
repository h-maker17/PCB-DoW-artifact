"""Real AutoGen 0.7.5 + official OpenAI client, with passive request tracing."""
import argparse
import ast
import asyncio
import contextvars
import dataclasses
import hashlib
import json
import operator
import os
import shlex
import signal
import sys
import time
import uuid
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import httpx
from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.base import Handoff
from autogen_agentchat.conditions import TextMentionTermination
from autogen_agentchat.messages import HandoffMessage, TextMessage
from autogen_agentchat.teams import Swarm
from autogen_core import CancellationToken
from autogen_core.model_context import (
    UnboundedChatCompletionContext,
    BufferedChatCompletionContext,
    HeadAndTailChatCompletionContext,
    TokenLimitedChatCompletionContext,
)
from autogen_ext.models.openai import OpenAIChatCompletionClient

INFO = dict(vision=False, function_calling=True, json_output=True,
            family="unknown", structured_output=False)
ACTIVE_CALL = contextvars.ContextVar("active_call", default=None)
BASE = Path("/path/to/workspace")
# AutoGen's count_tokens() falls back to this fixed encoding for unknown "qwen3-8b".
# This only decides TokenLimited's trimming; measured tokens always come from vLLM.
os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(Path(__file__).resolve().parent / "offline/tiktoken-cache"))
DEFAULT_TASK = ("请完成一次协作测试：Planner交给worker；worker用calculate计算"
                "(12+18)*3，将结果写入answer.txt，然后交给reviewer；"
                "reviewer读取文件并重新计算核对，给出中文结果。")

CONTEXT_DEFAULTS = {
    "unbounded": {},
    "buffered": {"buffer_size": 6},
    "headtail": {"head_size": 2, "tail_size": 2},
    "tokenlimited": {"token_limit": 800},
}


def context_config(kind, value=None):
    """Resolve the documented defaults and optional per-context override."""
    if kind not in CONTEXT_DEFAULTS:
        raise ValueError(f"Unknown context: {kind}")
    config = dict(CONTEXT_DEFAULTS[kind])
    if value is None:
        return config
    if kind == "unbounded":
        raise ValueError("--ctx-param is not applicable to unbounded")
    if kind == "headtail":
        parts = value.split(",")
        if len(parts) != 2:
            raise ValueError("headtail --ctx-param must be HEAD,TAIL, e.g. 2,2")
        head, tail = (int(x) for x in parts)
        if min(head, tail) <= 0:
            raise ValueError("headtail sizes must be positive")
        return {"head_size": head, "tail_size": tail}
    number = int(value)
    if number <= 0:
        raise ValueError("--ctx-param must be positive")
    key = "buffer_size" if kind == "buffered" else "token_limit"
    return {key: number}


def make_context(kind, model_client, value=None):
    config = context_config(kind, value)
    if kind == "unbounded":
        return UnboundedChatCompletionContext()
    if kind == "buffered":
        return BufferedChatCompletionContext(**config)
    if kind == "headtail":
        return HeadAndTailChatCompletionContext(**config)
    return TokenLimitedChatCompletionContext(model_client=model_client, **config)


async def context_diagnostics(contexts, kind, params):
    rows = {}
    for name, context in contexts.items():
        full = (await context.save_state())["messages"]
        visible = await context.get_messages()
        rows[name] = dict(context=kind, parameters=params,
                          accumulated_messages=len(full), visible_messages=len(visible),
                          truncation_observed_at_end=len(visible) < len(full),
                          visible_message_types=[type(message).__name__ for message in visible])
    return rows


def serial(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if dataclasses.is_dataclass(value):
        return {f.name: serial(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, (list, tuple)):
        return [serial(x) for x in value]
    if isinstance(value, dict):
        return {k: serial(v) for k, v in value.items() if k != "cancellation_token"}
    if hasattr(value, "schema"):
        return value.schema
    return value


def write_json(path, data):
    Path(path).write_text(json.dumps(serial(data), ensure_ascii=False, indent=2), encoding="utf-8")


def usage_of(response):
    """Missing/invalid telemetry remains unknown, never estimated as zero."""
    u = response.get("usage") or {}
    prompt, completion, total = (u.get(k) for k in
                                ("prompt_tokens", "completion_tokens", "total_tokens"))
    details = u.get("prompt_tokens_details") or {}
    hit = details.get("cached_tokens")
    valid = (type(prompt) is int and type(hit) is int and 0 <= hit <= prompt)
    ids = response.get("prompt_token_ids")
    return dict(prompt_tokens=prompt, cached_tokens=hit if valid else None,
                uncached_tokens=prompt - hit if valid else None,
                completion_tokens=completion, total_tokens=total,
                cache_telemetry_status="reported" if valid else "unknown",
                prompt_ids_match_usage=(len(ids) == prompt) if isinstance(ids, list) else None,
                completion_tokens_details=u.get("completion_tokens_details"),
                raw_usage=u)


class Trace:
    def __init__(self, dest, url, model, allow_code=False, transport=None, max_calls=48):
        self.dest = Path(dest)
        self.dest.mkdir(parents=True, exist_ok=False)
        self.workspace = self.dest / "workspace"
        self.workspace.mkdir()
        self.url, self.model, self.allow_code = url.rstrip("/"), model, allow_code
        self.transport, self.max_calls = transport, max_calls
        self.salt = uuid.uuid4().hex
        self.clients, self.records, self.agents, self.events = [], [], [], []
        self.attempts = []
        self.sequence = 0

    def event(self, kind, **payload):
        row = dict(time_utc=datetime.now(timezone.utc).isoformat(), kind=kind, **serial(payload))
        self.events.append(row)
        with (self.dest / "events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def next_call(self, agent):
        if self.sequence >= self.max_calls:
            raise RuntimeError("Model request budget exhausted; see saved trace.")
        self.sequence += 1
        call = f"{self.sequence:04d}-{agent}"
        self.attempts.append(dict(call=call, agent=agent))
        return call

    def client(self, agent):
        async def request_hook(request):
            call = ACTIVE_CALL.get()
            if call is None:
                raise RuntimeError("Unattributed model request")
            write_json(self.dest / f"{call}.request.json", json.loads(request.content))

        async def response_hook(response):
            call = ACTIVE_CALL.get()
            await response.aread()
            try:
                body = response.json()
            except ValueError:
                body = dict(error=response.text)
            write_json(self.dest / f"{call}.response.json", body)
            row = dict(call=call, agent=agent, http_status=response.status_code,
                       **usage_of(body))
            self.records.append(row)
            write_json(self.dest / "usage.json", self.records)

        http = httpx.AsyncClient(event_hooks=dict(request=[request_hook], response=[response_hook]),
                                 transport=self.transport, timeout=300)
        client = TracedClient(trace=self, agent=agent, model=self.model, model_info=INFO,
                              base_url=self.url + "/v1", api_key="local-only",
                              http_client=http, max_retries=0, timeout=300,
                              temperature=0, seed=42, max_tokens=2048,
                              parallel_tool_calls=False, include_name_in_message=True,
                              add_name_prefixes=False,
                              extra_body=dict(return_token_ids=True, cache_salt=self.salt,
                                              chat_template_kwargs=(dict(enable_thinking=False) if self.model.startswith("qwen") else {})))
        self.clients.append(client)
        return client

    async def close(self):
        for client in self.clients:
            await client.close()
        totals = {}
        for key in ("prompt_tokens", "cached_tokens", "uncached_tokens", "completion_tokens", "total_tokens"):
            values = [r[key] for r in self.records]
            totals[key] = sum(values) if (values and len(values) == len(self.attempts)
                                         and all(type(v) is int for v in values)) else None
        totals["requests"] = len(self.records)
        totals["attempted_requests"] = len(self.attempts)
        totals["requests_without_response"] = len(self.attempts) - len(self.records)
        totals["unknown_cache_requests"] = sum(r["cache_telemetry_status"] != "reported" for r in self.records)
        write_json(self.dest / "totals.json", totals)
        by_agent = {}
        for agent in sorted({r["agent"] for r in self.attempts}):
            rows = [r for r in self.records if r["agent"] == agent]
            attempts = sum(r["agent"] == agent for r in self.attempts)
            counts = {}
            for key in ("prompt_tokens", "cached_tokens", "uncached_tokens", "completion_tokens", "total_tokens"):
                values = [r[key] for r in rows]
                counts[key] = sum(values) if (len(values) == attempts
                                              and all(type(v) is int for v in values)) else None
            by_agent[agent] = dict(requests=len(rows), attempted_requests=attempts, **counts)
        write_json(self.dest / "usage-by-agent.json", by_agent)
        print("Token totals:", json.dumps(totals, ensure_ascii=False))
        print("Trace:", str(self.dest.resolve()))


class TracedClient(OpenAIChatCompletionClient):
    """Pass through to the official client; do not rewrite messages or tool schemas."""
    def __init__(self, *, trace, agent, **kwargs):
        super().__init__(**kwargs)
        self.trace, self.agent = trace, agent

    async def create(self, messages, **kwargs):
        call = self.trace.next_call(self.agent)
        write_json(self.trace.dest / f"{call}.typed.json",
                   dict(agent=self.agent, messages=messages, tools=kwargs.get("tools", [])))
        token = ACTIVE_CALL.set(call)
        start = time.monotonic()
        try:
            result = await super().create(messages, **kwargs)
            write_json(self.trace.dest / f"{call}.create-result.json", result)
            return result
        finally:
            self.trace.event("model_call_end", call=call, agent=self.agent,
                             elapsed_seconds=time.monotonic() - start)
            ACTIVE_CALL.reset(token)


def workspace_path(trace, name):
    root = trace.workspace.resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError("File must be inside this run's workspace.")
    return path


def tools_for(trace):
    async def calculate(expression: str) -> str:
        """Compute arithmetic using numbers, +, -, *, /, parentheses. No Python eval."""
        if len(expression) > 1000:
            raise ValueError("Expression too long")
        def visit(n):
            if isinstance(n, ast.Constant) and type(n.value) in (int, float):
                value = n.value
            elif isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
                value = (-1 if isinstance(n.op, ast.USub) else 1) * visit(n.operand)
            elif isinstance(n, ast.BinOp) and type(n.op) in (ast.Add, ast.Sub, ast.Mult, ast.Div):
                op = {ast.Add: operator.add, ast.Sub: operator.sub,
                      ast.Mult: operator.mul, ast.Div: operator.truediv}[type(n.op)]
                value = op(visit(n.left), visit(n.right))
            else:
                raise ValueError("Unsupported arithmetic")
            if abs(value) > 1e100:
                raise ValueError("Number too large")
            return value
        result = str(visit(ast.parse(expression, mode="eval").body))
        trace.event("tool", tool="calculate", expression=expression, result=result)
        return result

    async def write_file(path: str, content: str) -> str:
        """Write a UTF-8 text file in this run's workspace."""
        if len(content) > 200000:
            raise ValueError("File too large")
        dest = workspace_path(trace, path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        trace.event("tool", tool="write_file", path=path, bytes=dest.stat().st_size)
        return "Saved " + path

    async def read_file(path: str) -> str:
        """Read up to 20,000 characters of a workspace text file."""
        with workspace_path(trace, path).open(encoding="utf-8") as f:
            result = f.read(20000)
        trace.event("tool", tool="read_file", path=path, result=result)
        return result

    async def list_files() -> str:
        """List the first 200 files or directories in this run's workspace."""
        import itertools
        return json.dumps([str(p.relative_to(trace.workspace)) for p in
                           itertools.islice(trace.workspace.rglob("*"), 200)], ensure_ascii=False)

    async def run_command(command: str) -> str:
        """Execute pwd, date, or uname (no arguments); no shell syntax is evaluated."""
        argv = shlex.split(command)
        if argv not in [["pwd"], ["date"], ["uname"]]:
            raise ValueError("Available commands: pwd, date, uname. Use the file tools for files.")
        return await run_process(argv, trace)

    async def python_code(code: str) -> str:
        """Run Python in the run workspace, with a 20-second timeout. Opt-in host code execution."""
        if len(code) > 20000:
            raise ValueError("Code too long")
        return await run_process([sys.executable, "-I", "-c", code], trace)

    result = [calculate, write_file, read_file, list_files, run_command]
    if trace.allow_code:
        result.append(python_code)
    return result


async def run_process(argv, trace):
    # Truncate captured output by reading continuously; avoid unbounded communicate().
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=str(trace.workspace), stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        start_new_session=(os.name != "nt"))
    chunks = bytearray()
    async def drain():
        while block := await proc.stdout.read(4096):
            if len(chunks) < 20000:
                chunks.extend(block[:20000 - len(chunks)])
        await proc.wait()
    timed_out = False
    try:
        await asyncio.wait_for(drain(), 20)
    except (asyncio.TimeoutError, asyncio.CancelledError) as error:
        if os.name != "nt":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif proc.returncode is None:
            proc.kill()
        await proc.wait()
        if isinstance(error, asyncio.CancelledError):
            raise
        timed_out = True
    result = dict(exit_code=proc.returncode, timed_out=timed_out,
                  output=chunks.decode("utf-8", errors="replace"))
    trace.event("tool", tool="process", argv=argv, result=result)
    return json.dumps(result, ensure_ascii=False)


async def swarm_run(trace, task, context="unbounded", ctx_param=None):
    params = context_config(context, ctx_param)
    planner_client = trace.client("planner")
    worker_client = trace.client("worker")
    reviewer_client = trace.client("reviewer")
    contexts = {name: make_context(context, client, ctx_param) for name, client in (
        ("planner", planner_client), ("worker", worker_client), ("reviewer", reviewer_client))}
    planner = AssistantAgent("planner", model_client=planner_client,
        model_context=contexts["planner"],
        system_message="PLANNER_SYSTEM: You plan tasks. Transfer to worker immediately. Do not solve the task yourself.",
        handoffs=[Handoff(target="worker", message="HANDOFF_PLANNER_TO_WORKER: Execute the user's task with tools.")])
    worker = AssistantAgent("worker", model_client=worker_client, tools=tools_for(trace),
        model_context=contexts["worker"],
        system_message=("WORKER_SYSTEM: Execute the original task with tools. Check tool errors. "
                        "When work is ready, transfer to reviewer. Files persist in the shared run workspace."),
        handoffs=[Handoff(target="reviewer", message="HANDOFF_WORKER_TO_REVIEWER: Inspect the workspace and verify the task.")],
        max_tool_iterations=6, reflect_on_tool_use=False)
    reviewer = AssistantAgent("reviewer", model_client=reviewer_client, tools=tools_for(trace),
        model_context=contexts["reviewer"],
        system_message=("REVIEWER_SYSTEM: Independently check original task and files with tools. "
                        "Do not assume that hidden worker reasoning or earlier tool calls were shared. "
                        "Respond in Chinese with evidence and any errors, then write LAB_DONE."),
        max_tool_iterations=6, reflect_on_tool_use=True)
    team = Swarm([planner, worker, reviewer],
                 termination_condition=TextMentionTermination("LAB_DONE", sources=["reviewer"]),
                 max_turns=12)
    try:
        async for event in team.run_stream(task=task):
            if hasattr(event, "messages"):
                write_json(trace.dest / "task-result.json", dict(messages=event.messages,
                                                               stop_reason=event.stop_reason))
            else:
                trace.event("autogen_event", event=event)
                print(type(event).__name__, getattr(event, "source", ""), str(getattr(event, "content", ""))[:1500])
    finally:
        write_json(trace.dest / "team-state.json", await team.save_state())
        write_json(trace.dest / "context-diagnostics.json",
                   await context_diagnostics(contexts, context, params))
    handoffs = [e["event"] for e in trace.events
                if e["kind"] == "autogen_event" and e["event"].get("type") == "HandoffMessage"]
    write_json(trace.dest / "handoffs.json", handoffs)
    if not handoffs:
        print("WARNING: No native handoff was observed; this run does not demonstrate handoff.")


async def dynamic_run(trace, task, context="unbounded", ctx_param=None):
    """Application-level factory; instances are real AssistantAgents, not Swarm membership mutation."""
    agents = {}
    contexts = {}
    params = context_config(context, ctx_param)
    async def delegate_to_new_agent(role: str, instructions: str, task: str) -> str:
        """Create a fresh AutoGen specialist with its own context and tools, execute a task, return its report."""
        if len(agents) >= 4:
            return "Agent creation limit reached (4)."
        name = f"specialist_{len(agents) + 1}"
        client = trace.client(name)
        contexts[name] = make_context(context, client, ctx_param)
        agent = AssistantAgent(name, model_client=client, tools=tools_for(trace),
            model_context=contexts[name],
            system_message=f"You are a specialist. Role: {role}\n{instructions}\nUse tools to verify your answer.",
            max_tool_iterations=6, reflect_on_tool_use=True)
        agents[name] = agent
        trace.event("agent_created", name=name, role=role, instructions=instructions,
                    mechanism="application factory, not native Swarm handoff")
        response = await agent.on_messages([TextMessage(content=task, source="planner")], CancellationToken())
        trace.event("specialist_response", name=name, response=response)
        return json.dumps(dict(agent=name, report=response.chat_message.to_text()), ensure_ascii=False)
    planner_client = trace.client("planner")
    contexts["planner"] = make_context(context, planner_client, ctx_param)
    planner = AssistantAgent("planner", model_client=planner_client,
        model_context=contexts["planner"],
        tools=[delegate_to_new_agent] + tools_for(trace),
        system_message=("You are Planner. Dynamically create specialists using delegate_to_new_agent. "
                        "Use at least one executor and one independent reviewer when appropriate. "
                        "Give them complete task details because they have independent contexts. "
                        "Coordinate through tool reports and workspace files. Summarize evidence in Chinese."),
        max_tool_iterations=8, reflect_on_tool_use=True)
    try:
        response = await planner.on_messages([TextMessage(content=task, source="user")], CancellationToken())
        write_json(trace.dest / "task-result.json", response)
        print(response.chat_message.to_text())
    finally:
        write_json(trace.dest / "team-state.json", dict(
            planner=await planner.save_state(),
            specialists={name: await agent.save_state() for name, agent in agents.items()}))
        write_json(trace.dest / "context-diagnostics.json",
                   await context_diagnostics(contexts, context, params))


async def inspect_prompts(trace):
    async with httpx.AsyncClient(base_url=trace.url, timeout=120, transport=trace.transport) as client:
        for file in sorted(trace.dest.glob("*.request.json")):
            prefix = file.name.removesuffix(".request.json")
            response_file = trace.dest / f"{prefix}.response.json"
            if not response_file.exists():
                continue
            response = json.loads(response_file.read_text(encoding="utf-8"))
            ids = response.get("prompt_token_ids")
            if not isinstance(ids, list):
                trace.event("inspection_unavailable", call=prefix, reason="server omitted prompt_token_ids")
                continue
            request = json.loads(file.read_text(encoding="utf-8"))
            payload = {k: request[k] for k in ("model", "messages", "tools", "chat_template_kwargs")
                       if k in request}
            try:
                r = await client.post("/tokenize", json=payload)
                r.raise_for_status()
                tokenized = r.json()
                write_json(trace.dest / f"{prefix}.tokenize.json", tokenized)
                r = await client.post("/detokenize", json=dict(model=trace.model, tokens=ids))
                r.raise_for_status()
                rendered = r.json()["prompt"]
                (trace.dest / f"{prefix}.prompt.txt").write_text(rendered, encoding="utf-8")
                trace.event("prompt_verified", call=prefix, actual_prompt_tokens=len(ids),
                            tokenize_matches_actual=(tokenized["tokens"] == ids),
                            token_ids_sha256=hashlib.sha256(json.dumps(ids).encode()).hexdigest())
            except Exception as exc:
                trace.event("inspection_error", call=prefix, error=str(exc))


async def main(args):
    if any(version(p) != "0.7.5" for p in ("autogen-agentchat", "autogen-core", "autogen-ext")):
        raise RuntimeError("This lab requires AutoGen 0.7.5 for reproducible source semantics.")
    params = context_config(args.context, args.ctx_param)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + args.mode + "-" + uuid.uuid4().hex[:6]
    trace = Trace(Path(args.output) / run_id, args.url, args.model, args.allow_code)
    write_json(trace.dest / "manifest.json", dict(mode=args.mode, task=args.task,
        versions={p: version(p) for p in ("autogen-agentchat", "autogen-core", "autogen-ext", "openai", "httpx")},
        url=args.url, model=args.model, cache_salt=trace.salt,
        allow_code=args.allow_code, natural_model_run=True,
        context=dict(kind=args.context, **params)))
    try:
        await (swarm_run(trace, args.task, args.context, args.ctx_param)
               if args.mode == "swarm" else dynamic_run(trace, args.task, args.context, args.ctx_param))
    except BaseException as exc:
        trace.event("run_error", error=repr(exc))
        raise
    finally:
        try:
            await inspect_prompts(trace)
        finally:
            await trace.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["swarm", "dynamic"], default="swarm")
    parser.add_argument("--task", default=DEFAULT_TASK)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--model", default="qwen3-8b")
    parser.add_argument("--output", default=str(BASE / "多智能体系统实验数据" / "autogen-handoff-runs"))
    parser.add_argument("--allow-code", action="store_true")
    parser.add_argument("--context", choices=tuple(CONTEXT_DEFAULTS), default="unbounded")
    parser.add_argument("--ctx-param", help="buffer size, token limit, or HEAD,TAIL for headtail")
    asyncio.run(main(parser.parse_args()))
