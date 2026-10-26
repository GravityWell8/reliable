"""指数退避 + 抖动。

W1 范围：确定性指数退避，抖动在 ±jitter 区间内均匀取值。
设计动机（来自 Armada 故障检测经验）：不带抖动的固定退避会让
大量客户端在同一时刻同步重试，形成重试风暴。
"""

from __future__ import annotations

import random
from typing import Callable


def exp_backoff(
    attempt: int,
    base: float = 0.2,
    factor: float = 2.0,
    jitter: float = 0.1,
    rng: Callable[[], float] = random.random,
) -> float:
    """计算第 attempt 次重试前的等待秒数。

    attempt 从 0 开始；delay = base * factor**attempt，再乘以
    (1 + jitter * U(-1, 1)) 的抖动因子。jitter=0 时结果确定。
    """
    if attempt < 0:
        raise ValueError("attempt must be >= 0")
    if base < 0 or factor <= 0:
        raise ValueError("base must be >= 0 and factor must be > 0")
    delay = base * (factor**attempt)
    return max(0.0, delay * (1.0 + jitter * (rng() * 2.0 - 1.0)))
