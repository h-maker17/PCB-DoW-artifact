"""Structural fixtures for Part B; these are not GPU cache measurements."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import httpx

from shared_prefix_lab import analyze_run, lcp, run, shared_document, worker_prompt


def fixture():
    return dict(id="fixture", object="chat.completion", created=1, model="qwen3-8b",
                choices=[dict(index=0, message=dict(role="assistant", content="Record 01."),
                              finish_reason="stop")],
                usage=dict(prompt_tokens=4, completion_tokens=2, total_tokens=6,
                           prompt_tokens_details=dict(cached_tokens=0)),
                prompt_token_ids=[1, 2, 3, 4])


class SharedPrefixTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_autogen_workers_have_identical_system_and_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            requests = []

            def handler(request):
                if request.url.path.endswith("/chat/completions"):
                    requests.append(json.loads(request.content))
                    return httpx.Response(200, json=fixture())
                if request.url.path == "/tokenize":
                    body = json.loads(request.content)
                    words = len(body["messages"][-1]["content"].split())
                    return httpx.Response(200, json=dict(tokens=list(range(words + 1)), count=words + 1))
                if request.url.path == "/detokenize":
                    return httpx.Response(200, json=dict(prompt="fixture"))
                raise AssertionError(request.url.path)

            args = SimpleNamespace(output=tmp, url="http://local", model="qwen3-8b",
                                   workers=3, asset=Path(__file__).parent / "assets/shared_doc.txt",
                                   shared_tokens=2000, variance="none")
            dest = await run(args, transport=httpx.MockTransport(handler))
            self.assertEqual(len(requests), 3)
            self.assertEqual(len({r["cache_salt"] for r in requests}), 1)
            self.assertEqual([r["messages"][0] for r in requests],
                             [requests[0]["messages"][0]] * 3)
            self.assertEqual([r["tools"] for r in requests], [requests[0]["tools"]] * 3)
            self.assertEqual(len(requests[0]["tools"]), 5)
            user_text = [r["messages"][1]["content"] for r in requests]
            common = (dest / "shared_doc.used.txt").read_text(encoding="utf-8")
            self.assertTrue(all(s.startswith(common) for s in user_text))
            self.assertGreaterEqual(len(common.split()), 1999)
            self.assertEqual(json.loads((dest / "manifest.json").read_text())
                             ["shared_tokens_calibrated_no_tools"], 2000)
            self.assertEqual(len(set(user_text)), 3)
            self.assertTrue(all(r["temperature"] == 0 and r["seed"] == 42 for r in requests))
            self.assertTrue(all(r["chat_template_kwargs"] == {"enable_thinking": False}
                                for r in requests))
            self.assertTrue(all(r["parallel_tool_calls"] is False for r in requests))
            self.assertTrue(all((dest / f"worker_{i}.result.json").exists()
                                for i in range(1, 4)))
            self.assertEqual(json.loads((dest / "shared-prefix-summary.json").read_text())
                             ["status"], "fixture")
            self.assertFalse(json.loads((dest / "manifest.json").read_text())
                             ["natural_model_run"])

    def test_variance_location_and_exact_lcp(self):
        doc = "ALPHA BETA GAMMA"
        none = [worker_prompt(doc, "task", i, "none") for i in (1, 2)]
        prefix = [worker_prompt(doc, "task", i, "prefix") for i in (1, 2)]
        suffix = [worker_prompt(doc, "task", i, "suffix") for i in (1, 2)]
        self.assertEqual(none[0], none[1])
        self.assertFalse(prefix[0].startswith(doc))
        self.assertEqual(lcp(prefix[0], prefix[1]), 1)
        self.assertTrue(all(s.startswith(doc) for s in suffix))
        self.assertNotEqual(suffix[0], suffix[1])
        self.assertTrue(suffix[0].index("\u200b") > suffix[0].index("Your subtask:"))
        midfix = [worker_prompt(doc, "task", i, "midfix") for i in (1, 2)]
        self.assertTrue(all(s.startswith("ALPHA") for s in midfix))
        self.assertTrue(all("GAMMA" in s for s in midfix))
        self.assertNotEqual(midfix[0], midfix[1])
        self.assertNotEqual(worker_prompt(doc, "task", 1, "prefix", "short"),
                            worker_prompt(doc, "task", 2, "prefix", "short"))

    def test_analyzer_requires_cold_start_and_real_telemetry(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            (dest / "manifest.json").write_text('{"natural_model_run": true}')
            rows = [
                dict(agent="worker_1", call="0001-worker_1", prompt_tokens=100,
                     cached_tokens=0, uncached_tokens=100, completion_tokens=2, total_tokens=102),
                dict(agent="worker_2", call="0002-worker_2", prompt_tokens=100,
                     cached_tokens=80, uncached_tokens=20, completion_tokens=2, total_tokens=102),
            ]
            (dest / "usage.json").write_text(json.dumps(rows))
            for name, ids in ((rows[0]["call"], list(range(100))),
                              (rows[1]["call"], list(range(80)) + list(range(1000, 1020)))):
                (dest / f"{name}.response.json").write_text(
                    json.dumps(dict(prompt_token_ids=ids)))
            self.assertEqual(analyze_run(dest, 2)["status"], "go")
            rows[1]["cached_tokens"] = None
            (dest / "usage.json").write_text(json.dumps(rows))
            self.assertEqual(analyze_run(dest, 2)["status"], "inconclusive")
            rows[1]["cached_tokens"] = 20
            (dest / "usage.json").write_text(json.dumps(rows))
            self.assertEqual(analyze_run(dest, 2)["status"], "no-go")


if __name__ == "__main__":
    unittest.main(verbosity=2)
