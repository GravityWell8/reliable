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


if __name__ == "__main__":
    unittest.main()
