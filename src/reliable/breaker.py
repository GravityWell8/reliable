"""滑动窗口熔断器：CLOSED -> OPEN -> HALF_OPEN -> CLOSED 三态状态机。

设计取舍（来自 Armada RES-002 的教训，写在这里防止将来自己忘）：

1. 熔断判定看"滑动窗口失败率"，不看连续失败次数。
   连续 N 次失败才熔断的策略对"偶发抖动 + 低流量"场景过于迟钝，
   对"突发风暴"又过于敏感；窗口失败率对两者都更稳。

2. HALF_OPEN 的放行不设并发名额闸门（probe 语义 = 连续成功 N 次才闭合）。
   名额闸门在探针调用挂死（未走到 record）时会死锁在 half_open，
   RES-002 里"idle 看门狗假阳性"就是同类坑。v0.1 用简单语义换正确性，
   并发闸门放路线图。

3. 时钟与随机源可注入，测试不 sleep。
"""

from __future__ import annotations

import random
import threading
import time
from collections import deque
from enum import Enum
from typing import Callable

from .exceptions import CircuitOpenError


class State(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """滑动窗口失败率熔断器。

    参数:
        failure_rate: 窗口内失败率达到该值即熔断（0 < r <= 1）。
        window:       滑动窗口样本数（成功+失败），deque 自动滑动。
        cooldown:     OPEN 后等待多少秒才允许半开探测。
        probe:        HALF_OPEN 下需要连续成功多少次才回到 CLOSED。
        clock/rng:    可注入时钟与随机源（测试用）。
    """

    def __init__(
        self,
        failure_rate: float = 0.5,
        window: int = 20,
        cooldown: float = 30.0,
        probe: int = 3,
        clock: Callable[[], float] = time.monotonic,
        rng: Callable[[], float] = random.random,
    ) -> None:
        if not 0 < failure_rate <= 1:
            raise ValueError("failure_rate must be in (0, 1]")
        if window < 1:
            raise ValueError("window must be >= 1")
        if cooldown < 0:
            raise ValueError("cooldown must be >= 0")
        if probe < 1:
            raise ValueError("probe must be >= 1")
        self.failure_rate = failure_rate
        self.window = window
        self.cooldown = cooldown
        self.probe = probe
        self._clock = clock
        self._rng = rng
        self._lock = threading.Lock()
        self._results: deque[bool] = deque(maxlen=window)
        self._state = State.CLOSED
        self._opened_at: float | None = None
        self._half_open_results: list[bool] = []

    @property
    def state(self) -> State:
        with self._lock:
            return self._state

    def before_call(self) -> None:
        """调用前检查；OPEN 且未到冷却时间时抛 CircuitOpenError。"""
        with self._lock:
            now = self._clock()
            if self._state is State.OPEN:
                if self._opened_at is None:
                    self._opened_at = now
                elapsed = now - self._opened_at
                if elapsed >= self.cooldown:
                    self._state = State.HALF_OPEN
                    self._half_open_results = []
                else:
                    raise CircuitOpenError(
                        f"circuit is OPEN, retry after {self.cooldown - elapsed:.1f}s",
                        retry_after=self.cooldown - elapsed,
                    )
            # CLOSED / HALF_OPEN 均放行

    def record_success(self) -> None:
        with self._lock:
            if self._state is State.HALF_OPEN:
                self._half_open_results.append(True)
                if len(self._half_open_results) >= self.probe:
                    self._state = State.CLOSED
                    self._half_open_results = []
                    self._results.clear()
                    self._opened_at = None
            else:
                self._results.append(True)

    def record_failure(self) -> None:
        with self._lock:
            if self._state is State.HALF_OPEN:
                self._trip()
                return
            self._results.append(False)
            if len(self._results) >= self.window:
                failures = self._results.count(False)
                if failures / len(self._results) >= self.failure_rate:
                    self._trip()

    def _trip(self) -> None:
        self._state = State.OPEN
        self._opened_at = self._clock()
        self._half_open_results = []

    def stats(self) -> dict:
        """返回当前状态与窗口统计，便于观测/接入 MCP 资源。

        返回字段：state（closed/open/half_open）、failure_rate_threshold、
        window、window_total、window_failures、window_failure_rate、
        cooldown、probe、opened_at。
        """
        with self._lock:
            results = list(self._results)
            total = len(results)
            failures = results.count(False) if total else 0
            return {
                "state": self._state.value,
                "failure_rate_threshold": self.failure_rate,
                "window": self.window,
                "window_total": total,
                "window_failures": failures,
                "window_failure_rate": (failures / total) if total else 0.0,
                "cooldown": self.cooldown,
                "probe": self.probe,
                "opened_at": self._opened_at,
            }
