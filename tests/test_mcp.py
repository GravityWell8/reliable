"""MCP server 骨架冒烟测试：不启动 transport，只验证工具注册与纯逻辑。"""

import asyncio
import unittest

from reliable import mcp as mcp_server
from reliable.mcp import compute_backoff, create_breaker, get_breaker_state


class TestMcpSkeleton(unittest.TestCase):
    def test_tools_registered(self):
        tools = asyncio.run(mcp_server.mcp.list_tools())
        names = {t.name for t in tools}
        self.assertIn("create_breaker", names)
        self.assertIn("get_breaker_state", names)
        self.assertIn("compute_backoff", names)
        self.assertIn("reliable_http_call", names)

    def test_compute_backoff_deterministic(self):
        # jitter=0 -> 确定性；delay = base * factor**attempt
        self.assertAlmostEqual(compute_backoff(1, 0.2, 2.0, 0.0)["delay"], 0.4)
        self.assertAlmostEqual(compute_backoff(2, 0.2, 2.0, 0.0)["delay"], 0.8)

    def test_compute_backoff_jitter_bounds(self):
        out = compute_backoff(3, 1.0, 2.0, 0.5)
        delay = out["delay"]
        self.assertGreaterEqual(delay, 1.0 * (2**3) * 0.5)   # 1 - 0.5
        self.assertLessEqual(delay, 1.0 * (2**3) * 1.5)       # 1 + 0.5

    def test_create_and_query_breaker(self):
        created = create_breaker("test-llm", failure_rate=0.5, window=20, cooldown=30.0, probe=3)
        self.assertEqual(created["name"], "test-llm")
        self.assertEqual(created["state"], "closed")
        self.assertEqual(created["failure_rate_threshold"], 0.5)

        state = get_breaker_state("test-llm")
        self.assertEqual(state["state"], "closed")
        self.assertEqual(state["window"], 20)

    def test_query_unknown_breaker(self):
        out = get_breaker_state("does-not-exist")
        self.assertIn("error", out)

    def test_every_tool_declares_output_schema(self):
        """返回类型必须是 TypedDict/Pydantic，否则 MCP 协议里没有 outputSchema。"""
        tools = asyncio.run(mcp_server.mcp.list_tools())
        self.assertTrue(tools)
        for t in tools:
            with self.subTest(tool=t.name):
                schema = t.output_schema
                self.assertIsNotNone(schema, f"{t.name} 缺少 outputSchema")
                self.assertEqual(schema.get("type"), "object")
                self.assertTrue(schema.get("properties"), f"{t.name} 的 outputSchema 没有字段")

    def test_required_fields_match_actual_returns(self):
        """required 只应包含恒存在的字段，别把分支字段标成必需。"""
        tools = {t.name: t for t in asyncio.run(mcp_server.mcp.list_tools())}
        self.assertEqual(tools["create_breaker"].output_schema["required"], ["name", "created"])
        self.assertEqual(tools["get_breaker_state"].output_schema["required"], ["name"])
        self.assertEqual(tools["compute_backoff"].output_schema["required"], ["attempt", "delay"])
        self.assertEqual(tools["reliable_http_call"].output_schema["required"], ["ok", "breaker"])


if __name__ == "__main__":
    unittest.main()
