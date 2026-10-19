"""Local transport tests. HTTP responses are fixtures, not Qwen/GPU measurements."""
import asyncio
import ast
import json
import tempfile
import unittest
from pathlib import Path
import httpx
from lab import Trace, context_config, context_diagnostics, dynamic_run, make_context, serial, swarm_run, tools_for, usage_of
from probe import lcp, mutate
from server import patch_source
from autogen_core.models import UserMessage
from openai import APIConnectionError


def fixture(content=None, tool=None, arguments=None, *, cached=0):
    msg = dict(role="assistant", content=content)
    if tool:
        msg["tool_calls"] = [dict(id="id_" + tool, type="function",
                                  function=dict(name=tool, arguments=json.dumps(arguments or {})))]
    return dict(id="fixture", object="chat.completion", created=1, model="qwen3-8b",
                choices=[dict(index=0, message=msg, finish_reason="tool_calls" if tool else "stop")],
                usage=dict(prompt_tokens=3, completion_tokens=2, total_tokens=5,
                           prompt_tokens_details=dict(cached_tokens=cached)),
                prompt_token_ids=[1, 2, 3])


class LabTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, mode, responses):
        with tempfile.TemporaryDirectory() as tmp:
            payloads = []
            def handler(request):
                payloads.append(json.loads(request.content))
                return httpx.Response(200, json=responses.pop(0))
            trace = Trace(Path(tmp) / "run", "http://local", "qwen3-8b",
                          transport=httpx.MockTransport(handler))
            try:
                await (swarm_run(trace, "fixture task") if mode == "swarm" else dynamic_run(trace, "fixture task"))
                self.assertEqual(responses, [])
                self.assertEqual(trace.sequence, len(payloads))
                self.assertTrue(all(p["return_token_ids"] for p in payloads))
                self.assertTrue(all(p["chat_template_kwargs"] == {"enable_thinking": False} for p in payloads))
                self.assertTrue(all(r["cached_tokens"] == 0 and r["uncached_tokens"] == 3 for r in trace.records))
                self.assertEqual(len(list(trace.dest.glob("*.typed.json"))), len(payloads))
                self.assertEqual(len(list(trace.dest.glob("*.request.json"))), len(payloads))
                self.assertTrue((trace.dest / "team-state.json").is_file())
                if mode == "swarm":
                    self.assertEqual((trace.workspace / "answer.txt").read_text(), "90")
                    handoffs = json.loads((trace.dest / "handoffs.json").read_text())
                    self.assertEqual(len(handoffs), 2)
                    worker_call = payloads[1]
                    self.assertEqual(worker_call["messages"][0]["role"], "system")
                    self.assertEqual(worker_call["messages"][-1]["role"], "user")
                    self.assertIn("HANDOFF_PLANNER", worker_call["messages"][-1]["content"])
                    self.assertIn("tools", worker_call)
                else:
                    self.assertTrue(any(e["kind"] == "agent_created" for e in trace.events))
                    self.assertEqual(trace.records[0]["agent"], "planner")
                    self.assertEqual(trace.records[1]["agent"], "specialist_1")
                    self.assertEqual(trace.records[-1]["agent"], "planner")
            finally:
                await trace.close()

    async def test_native_swarm_official_client_and_tools(self):
        await self.exercise("swarm", [
            fixture(tool="transfer_to_worker"),
            fixture(tool="calculate", arguments=dict(expression="(12+18)*3")),
            fixture(tool="write_file", arguments=dict(path="answer.txt", content="90")),
            fixture(tool="transfer_to_reviewer"),
            fixture(tool="read_file", arguments=dict(path="answer.txt")),
            fixture(tool="calculate", arguments=dict(expression="(12+18)*3")),
            fixture(content="Checked 90. LAB_DONE"),
        ])

    async def test_dynamic_factory_official_agents(self):
        await self.exercise("dynamic", [
            fixture(tool="delegate_to_new_agent", arguments=dict(role="math", instructions="calculate",
                                                               task="2+3")),
            fixture(tool="calculate", arguments=dict(expression="2+3")),
            fixture(content="5"),
            fixture(content="Specialist returned 5."),
        ])

    async def test_workspace_and_arithmetic(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace = Trace(Path(tmp) / "run", "http://local", "qwen3-8b")
            tools = {t.__name__: t for t in tools_for(trace)}
            self.assertEqual(await tools["calculate"]("(12+18)*3"), "90")
            for expr in ["__import__('os')", "2**10000"]:
                with self.assertRaises(ValueError):
                    await tools["calculate"](expr)
            with self.assertRaises(ValueError):
                await tools["write_file"]("../escape", "no")
            with self.assertRaises(ValueError):
                await tools["run_command"]("uname; echo no")
            self.assertNotIn("python_code", tools)

    def test_missing_usage_is_not_zero(self):
        self.assertIsNone(usage_of({"usage": {"prompt_tokens": 10}})["cached_tokens"])
        self.assertIsNone(usage_of({"usage": {"prompt_tokens": 10}})["uncached_tokens"])
        bad = fixture()
        bad["usage"]["prompt_tokens_details"]["cached_tokens"] = 99
        self.assertEqual(usage_of(bad)["cache_telemetry_status"], "unknown")
        self.assertEqual(usage_of(fixture(cached=2))["uncached_tokens"], 1)

    async def test_connection_failure_does_not_report_zero_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            def handler(request):
                raise httpx.ConnectError("fixture disconnected", request=request)
            trace = Trace(Path(tmp) / "run", "http://local", "qwen3-8b",
                          transport=httpx.MockTransport(handler))
            client = trace.client("failed_agent")
            try:
                with self.assertRaises(APIConnectionError):
                    await client.create([UserMessage(content="test", source="user")])
            finally:
                await trace.close()
            totals = json.loads((trace.dest / "totals.json").read_text())
            self.assertEqual(totals["requests_without_response"], 1)
            self.assertIsNone(totals["total_tokens"])
            self.assertIsNone(totals["cached_tokens"])

    def test_lcp_and_one_variable_ablation(self):
        self.assertEqual(lcp([1, 2], [1, 3]), 1)
        request = {"messages": [{"role": "system", "content": "A"},
                                {"role": "user", "content": "HANDOFF_TEST"}],
                   "tools": [{"function": {"description": "tool"}}]}
        for v in ["system", "handoff-text", "tool-description"]:
            changed = mutate(request, v)
            self.assertNotEqual(request, changed)
        self.assertEqual(request["messages"][-1]["content"], "HANDOFF_TEST")

    async def test_context_window_semantics_and_independent_instances(self):
        self.assertEqual(context_config("buffered"), {"buffer_size": 6})
        self.assertEqual(context_config("headtail", "2,2"), {"head_size": 2, "tail_size": 2})
        self.assertEqual(context_config("tokenlimited", "1200"), {"token_limit": 1200})
        with self.assertRaises(ValueError):
            context_config("unbounded", "6")
        for kind, override in [("unbounded", None), ("buffered", "4"),
                               ("headtail", None), ("tokenlimited", "800")]:
            with tempfile.TemporaryDirectory() as tmp:
                trace = Trace(Path(tmp) / "run", "http://local", "qwen3-8b")
                try:
                    client = trace.client("test")
                    first = make_context(kind, client, override)
                    second = make_context(kind, client, override)
                    self.assertIsNot(first, second)
                    for number in range(9):
                        await first.add_message(UserMessage(content=f"message {number}", source="user"))
                    if kind == "tokenlimited":
                        # Exact threshold depends on the official client's estimate.
                        self.assertLessEqual(client.count_tokens(await first.get_messages()), 800)
                    elif kind == "unbounded":
                        self.assertEqual(len(await first.get_messages()), 9)
                    elif kind == "buffered":
                        self.assertEqual(len(await first.get_messages()), 4)
                    else:
                        visible = await first.get_messages()
                        self.assertEqual(len(visible), 5)
                        self.assertIn("Skipped 5 messages", visible[2].content)
                    diag = await context_diagnostics({"first": first, "second": second},
                                                     kind, context_config(kind, override))
                    self.assertEqual(diag["first"]["accumulated_messages"], 9)
                    self.assertEqual(diag["second"]["accumulated_messages"], 0)
                finally:
                    await trace.close()

    def test_metering_patch_only_changes_condition(self):
        # Parse the actually downloaded installed source; no vLLM import/GPU required.
        source = Path("upstream-vllm/serving_chat.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        method = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
                      and n.name == "chat_completion_full_generator")
        original, patched = patch_source(ast.get_source_segment(source, method))
        self.assertEqual(patched.count("num_cached_tokens is not None:"), 1)
        self.assertEqual(len(original.splitlines()), len(patched.splitlines()))
        compile("from __future__ import annotations\n" + patched, "<test>", "exec")
        for count, expected in [(None, False), (0, True), (16, True)]:
            self.assertEqual(count is not None, expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
