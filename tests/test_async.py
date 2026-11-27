"""async 路径测试：重试、熔断、真取消、降级，以及"静默失效"的回归防护。

背景：v0.1.4 及以前把 `async def` 传给同步装饰器时，wrapper 只拿到
coroutine 对象，既不 await 也不抛异常 → 记为成功 → 熔断器永远看到
失败率 0.00、永不熔断。对异步调用等于零保护，还谎报健康。
"""

import asyncio
import inspect
import unittest

from reliable import CircuitBreaker, CircuitOpenError, State, reliable


class TestAsyncRetry(unittest.IsolatedAsyncioTestCase):
    async def test_async_function_is_detected(self):
        @reliable(max_attempts=1, base_delay=0.0)
        async def f() -> str:
            return "ok"

        self.assertTrue(inspect.iscoroutinefunction(f))

    async def test_async_retries_and_returns_on_success(self):
        calls = []

        @reliable(max_attempts=4, base_delay=0.0, jitter=0.0)
        async def flaky() -> str:
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeError("503")
            return "ok"

        self.assertEqual(await flaky(), "ok")
        self.assertEqual(len(calls), 3)

    async def test_async_exhausts_attempts_and_raises_last_error(self):
        calls = []

        @reliable(max_attempts=3, base_delay=0.0, jitter=0.0)
        async def always_fails() -> None:
            calls.append(1)
            raise RuntimeError(f"boom-{len(calls)}")

        with self.assertRaises(RuntimeError) as ctx:
            await always_fails()
        self.assertEqual(len(calls), 3)
        self.assertIn("boom-3", str(ctx.exception))


class TestAsyncBreaker(unittest.IsolatedAsyncioTestCase):
    async def test_async_failures_are_recorded_not_reported_as_success(self):
        """回归：6 次真实失败，熔断器必须看到 6 次失败，失败率 1.0。"""
        breaker = CircuitBreaker(failure_rate=0.5, window=10)

        @reliable(breaker=breaker, max_attempts=1, base_delay=0.0)
        async def always_fails() -> None:
            raise RuntimeError("503")

        for _ in range(6):
            with self.assertRaises(RuntimeError):
                await always_fails()

        stats = breaker.stats()
        self.assertEqual(stats["window_total"], 6)
        self.assertEqual(stats["window_failures"], 6)
        self.assertEqual(stats["window_failure_rate"], 1.0)

    async def test_async_breaker_opens_and_short_circuits(self):
        breaker = CircuitBreaker(failure_rate=0.5, window=4, cooldown=30.0, probe=2)
        calls = []

        @reliable(breaker=breaker, max_attempts=1, base_delay=0.0)
        async def always_fails() -> None:
            calls.append(1)
            raise RuntimeError("503")

        for _ in range(4):
            with self.assertRaises(RuntimeError):
                await always_fails()
        self.assertIs(breaker.state, State.OPEN)

        # OPEN 且未到冷却：不应再打下游
        with self.assertRaises(CircuitOpenError):
            await always_fails()
        self.assertEqual(len(calls), 4)

    async def test_async_success_resets_window(self):
        breaker = CircuitBreaker(failure_rate=0.5, window=10)

        @reliable(breaker=breaker, max_attempts=1, base_delay=0.0)
        async def ok() -> str:
            return "fine"

        self.assertEqual(await ok(), "fine")
        stats = breaker.stats()
        self.assertEqual(stats["window_failures"], 0)
        self.assertIs(breaker.state, State.CLOSED)


class TestAsyncTimeoutIsRealCancellation(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_cancels_the_coroutine(self):
        """同步版超时是"放弃等待"，async 版必须是真取消。"""
        cancelled = []

        async def slow_body() -> None:
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                cancelled.append(True)
                raise

        wrapped = reliable(timeout=0.05, max_attempts=1, base_delay=0.0)(slow_body)

        with self.assertRaises(TimeoutError):
            await wrapped()

        self.assertEqual(cancelled, [True], "协程应收到 CancelledError 被真取消")

    async def test_timeout_counts_as_failure_then_retries(self):
        breaker = CircuitBreaker(failure_rate=0.9, window=10)
        attempts = []

        @reliable(breaker=breaker, timeout=0.02, max_attempts=2, base_delay=0.0)
        async def too_slow() -> None:
            attempts.append(1)
            await asyncio.sleep(5)

        with self.assertRaises(TimeoutError):
            await too_slow()

        self.assertEqual(len(attempts), 2, "超时也应触发重试")
        self.assertEqual(breaker.stats()["window_failures"], 2)


class TestAsyncFallback(unittest.IsolatedAsyncioTestCase):
    async def test_sync_on_open_callback(self):
        breaker = CircuitBreaker(failure_rate=1.0, window=1, cooldown=60.0, probe=1)

        @reliable(breaker=breaker, max_attempts=1, base_delay=0.0)
        async def always_fails() -> None:
            raise RuntimeError("503")

        for _ in range(1):
            with self.assertRaises(RuntimeError):
                await always_fails()
        self.assertIs(breaker.state, State.OPEN)

        @reliable(
            breaker=breaker,
            max_attempts=1,
            on_open=lambda *a, **k: "cached",
        )
        async def guarded() -> str:
            return "live"

        self.assertEqual(await guarded(), "cached")

    async def test_async_on_open_callback_is_awaited(self):
        breaker = CircuitBreaker(failure_rate=1.0, window=1, cooldown=60.0, probe=1)

        @reliable(breaker=breaker, max_attempts=1, base_delay=0.0)
        async def always_fails() -> None:
            raise RuntimeError("503")

        with self.assertRaises(RuntimeError):
            await always_fails()
        self.assertIs(breaker.state, State.OPEN)

        async def async_fallback(*a, **k) -> str:
            await asyncio.sleep(0)
            return "async-cached"

        @reliable(breaker=breaker, max_attempts=1, on_open=async_fallback)
        async def guarded() -> str:
            return "live"

        self.assertEqual(await guarded(), "async-cached")

    async def test_decorator_without_parentheses_on_async(self):
        @reliable
        async def ok() -> str:
            return "bare"

        self.assertEqual(await ok(), "bare")


class TestSilentFailureGuard(unittest.TestCase):
    def test_sync_function_returning_coroutine_raises(self):
        """第二类静默失效：同步函数里返回协程，必须显式报错而不是记成功。"""

        async def thing() -> str:
            return "x"

        @reliable(max_attempts=1, base_delay=0.0)
        def leaks_coroutine() -> object:
            return thing()

        with self.assertRaises(TypeError) as ctx:
            leaks_coroutine()
        self.assertIn("coroutine", str(ctx.exception))

    def test_timeout_must_be_positive(self):
        with self.assertRaises(ValueError):
            reliable(timeout=0)


if __name__ == "__main__":
    unittest.main()
