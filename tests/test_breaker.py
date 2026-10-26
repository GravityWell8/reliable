"""熔断器三态 + 滑动窗口测试。时钟注入，零真实等待。"""

import unittest

from reliable import CircuitBreaker, CircuitOpenError, State


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def make_breaker(**kw):
    clock = FakeClock()
    return CircuitBreaker(clock=clock, rng=lambda: 0.5, **kw), clock


class TestBreaker(unittest.TestCase):
    def test_initial_state_closed(self):
        breaker, _ = make_breaker()
        self.assertIs(breaker.state, State.CLOSED)

    def test_opens_when_failure_rate_exceeded(self):
        breaker, _ = make_breaker(window=4, failure_rate=0.5)
        for _ in range(4):
            breaker.before_call()
            breaker.record_failure()
        self.assertIs(breaker.state, State.OPEN)

    def test_open_calls_rejected_with_retry_after(self):
        breaker, _ = make_breaker(window=2, failure_rate=0.5, cooldown=30)
        for _ in range(2):
            breaker.before_call()
            breaker.record_failure()
        with self.assertRaises(CircuitOpenError) as ctx:
            breaker.before_call()
        self.assertAlmostEqual(ctx.exception.retry_after, 30.0)

    def test_half_open_after_cooldown(self):
        breaker, clock = make_breaker(window=2, failure_rate=0.5, cooldown=10)
        for _ in range(2):
            breaker.before_call()
            breaker.record_failure()
        clock.t = 10.0
        breaker.before_call()  # 不抛，进入 half_open
        self.assertIs(breaker.state, State.HALF_OPEN)

    def test_half_open_requires_consecutive_successes(self):
        breaker, clock = make_breaker(window=2, failure_rate=0.5, cooldown=10, probe=2)
        for _ in range(2):
            breaker.before_call()
            breaker.record_failure()
        clock.t = 10.0
        breaker.before_call()
        breaker.record_success()
        self.assertIs(breaker.state, State.HALF_OPEN)  # probe=2，1 次不够
        breaker.before_call()
        breaker.record_success()
        self.assertIs(breaker.state, State.CLOSED)

    def test_half_open_failure_reopens(self):
        breaker, clock = make_breaker(window=2, failure_rate=0.5, cooldown=10)
        for _ in range(2):
            breaker.before_call()
            breaker.record_failure()
        clock.t = 10.0
        breaker.before_call()
        breaker.record_failure()
        self.assertIs(breaker.state, State.OPEN)

    def test_sliding_window_never_opens_below_rate(self):
        breaker, _ = make_breaker(window=4, failure_rate=0.8)
        seq = [True, False, False, False, True, True]  # 75% -> 75% -> 50%
        for ok in seq:
            breaker.before_call()
            if ok:
                breaker.record_success()
            else:
                breaker.record_failure()
        self.assertIs(breaker.state, State.CLOSED)

    def test_invalid_params(self):
        with self.assertRaises(ValueError):
            CircuitBreaker(failure_rate=0)
        with self.assertRaises(ValueError):
            CircuitBreaker(window=0)
        with self.assertRaises(ValueError):
            CircuitBreaker(probe=0)


if __name__ == "__main__":
    unittest.main()
