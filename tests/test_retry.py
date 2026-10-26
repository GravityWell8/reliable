"""退避计算与重试循环测试。sleep 注入，零真实等待。"""

import unittest

from reliable import exp_backoff, reliable


class FakeSleeper:
    def __init__(self) -> None:
        self.delays = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


class TestBackoff(unittest.TestCase):
    def test_deterministic_without_jitter(self):
        self.assertAlmostEqual(exp_backoff(0, 0.2, 2.0, 0.0, rng=lambda: 0.5), 0.2)
        self.assertAlmostEqual(exp_backoff(1, 0.2, 2.0, 0.0, rng=lambda: 0.5), 0.4)
        self.assertAlmostEqual(exp_backoff(2, 0.2, 2.0, 0.0, rng=lambda: 0.5), 0.8)

    def test_jitter_bounds(self):
        low = exp_backoff(0, 0.4, 2.0, 0.5, rng=lambda: 0.0)
        high = exp_backoff(0, 0.4, 2.0, 0.5, rng=lambda: 1.0)
        self.assertAlmostEqual(low, 0.4 * 0.5)
        self.assertAlmostEqual(high, 0.4 * 1.5)

    def test_negative_attempt_rejected(self):
        with self.assertRaises(ValueError):
            exp_backoff(-1)


class TestRetry(unittest.TestCase):
    def test_eventual_success_with_growing_backoff(self):
        sleeper = FakeSleeper()
        calls = {"n": 0}

        @reliable(max_attempts=5, base_delay=0.1, factor=2.0, jitter=0.0, sleep=sleeper)
        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise ConnectionError("down")
            return "ok"

        self.assertEqual(flaky(), "ok")
        self.assertEqual(calls["n"], 3)
        self.assertEqual(len(sleeper.delays), 2)
        self.assertGreater(sleeper.delays[1], sleeper.delays[0])

    def test_attempts_exhausted_raises_last_error(self):
        sleeper = FakeSleeper()

        @reliable(max_attempts=3, base_delay=0.01, jitter=0.0, sleep=sleeper)
        def always_down():
            raise ConnectionError("down")

        with self.assertRaises(ConnectionError):
            always_down()
        self.assertEqual(len(sleeper.delays), 2)  # n-1 次退避

    def test_max_attempts_validation(self):
        with self.assertRaises(ValueError):
            reliable(max_attempts=0)


if __name__ == "__main__":
    unittest.main()
