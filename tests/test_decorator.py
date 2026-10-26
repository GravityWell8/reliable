"""装饰器组合行为：降级回调、开路抛错、超时记失败。"""

import time
import unittest

from reliable import CircuitBreaker, CircuitOpenError, State, reliable


def make_open_breaker() -> CircuitBreaker:
    breaker = CircuitBreaker(window=2, failure_rate=0.5, cooldown=60)
    for _ in range(2):
        breaker.before_call()
        breaker.record_failure()
    return breaker


class TestDecorator(unittest.TestCase):
    def test_on_open_returns_fallback_without_calling_fn(self):
        breaker = make_open_breaker()

        @reliable(breaker=breaker, on_open=lambda *a, **k: "fallback", max_attempts=3)
        def fn():
            raise AssertionError("fn should not be called when circuit is open")

        self.assertEqual(fn(), "fallback")

    def test_open_without_fallback_raises_circuit_open(self):
        breaker = make_open_breaker()

        @reliable(breaker=breaker, max_attempts=3)
        def fn():
            raise AssertionError("fn should not be called when circuit is open")

        with self.assertRaises(CircuitOpenError):
            fn()

    def test_timeout_counts_as_failure(self):
        breaker = CircuitBreaker(window=10, failure_rate=0.5)

        @reliable(breaker=breaker, timeout=0.05, max_attempts=1)
        def slow():
            time.sleep(0.2)
            return "late"

        with self.assertRaises(TimeoutError):
            slow()
        self.assertIs(breaker.state, State.CLOSED)  # 1/10 < 50%

    def test_success_recorded_and_returned(self):
        breaker = CircuitBreaker(window=4, failure_rate=0.5)

        @reliable(breaker=breaker, max_attempts=1)
        def ok():
            return 42

        self.assertEqual(ok(), 42)
        self.assertIs(breaker.state, State.CLOSED)

    def test_bare_decorator_usage(self):
        @reliable
        def plain():
            return "hi"

        self.assertEqual(plain(), "hi")


if __name__ == "__main__":
    unittest.main()
