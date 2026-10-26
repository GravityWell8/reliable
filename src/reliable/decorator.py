"""reliable 装饰器：timeout + 指数退避重试 + 熔断 + 降级回调。

用法::

    breaker = CircuitBreaker(failure_rate=0.5, window=20, cooldown=30.0, probe=3)

    @reliable(breaker=breaker, timeout=8.0, max_attempts=5,
              on_open=lambda *a, **k: cache_fallback())
    def call_llm(prompt: str) -> str:
        ...

已知取舍（v0.1）：
- timeout 语义是"放弃等待"——底层线程仍在运行，生产上请配合
  HTTP 客户端自身的超时；asyncio 原生版本在路线图。
"""

from __future__ import annotations

import functools
import random
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, TypeVar

from .backoff import exp_backoff
from .breaker import CircuitBreaker
from .exceptions import CircuitOpenError

F = TypeVar("F", bound=Callable[..., Any])


def reliable(
    fn: F | None = None,
    *,
    breaker: CircuitBreaker | None = None,
    timeout: float | None = None,
    max_attempts: int = 5,
    base_delay: float = 0.2,
    factor: float = 2.0,
    jitter: float = 0.1,
    sleep: Callable[[float], None] = time.sleep,
    rng: Callable[[], float] = random.random,
    on_open: Callable[..., Any] | None = None,
) -> Callable:
    """把同步函数包上超时 / 重试 / 熔断 / 降级。

    - 每次 attempt 前先问熔断器；OPEN 且未到冷却则抛 CircuitOpenError，
      若提供 on_open 则调用其返回值作为降级结果。
    - 任何异常（含超时）都记为一次失败并触发退避；成功记为成功。
    - 支持 ``@reliable`` 与 ``@reliable(...)`` 两种写法。
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    def decorator(f: F) -> F:
        @functools.wraps(f)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: BaseException | None = None
            for attempt in range(max_attempts):
                if breaker is not None:
                    try:
                        breaker.before_call()
                    except CircuitOpenError:
                        if on_open is not None:
                            return on_open(*args, **kwargs)
                        raise
                try:
                    result = _invoke(f, args, kwargs, timeout)
                except Exception as exc:
                    if breaker is not None:
                        breaker.record_failure()
                    last_exc = exc
                    if attempt < max_attempts - 1:
                        sleep(exp_backoff(attempt, base_delay, factor, jitter, rng))
                else:
                    if breaker is not None:
                        breaker.record_success()
                    return result
            assert last_exc is not None
            raise last_exc

        return wrapper  # type: ignore[return-value]

    if fn is not None:
        return decorator(fn)
    return decorator


def _invoke(
    fn: Callable[..., Any],
    args: tuple,
    kwargs: dict,
    timeout: float | None,
) -> Any:
    if timeout is None:
        return fn(*args, **kwargs)
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(fn, *args, **kwargs)
        return future.result(timeout=timeout)
    finally:
        executor.shutdown(wait=False)
