"""reliable 装饰器：timeout + 指数退避重试 + 熔断 + 降级回调。

同步与异步共用同一套内核（同一份熔断器、同一份退避公式），区别只在
"如何等待结果"和"如何睡"：

- 同步函数：线程池 + ``future.result(timeout=...)``，语义是**放弃等待**
  （底层线程仍在跑，生产上请配合 HTTP 客户端自身超时）。
- 异步函数：``asyncio.wait_for`` + ``asyncio.sleep``，语义是**真取消**
  （协程收到 CancelledError，不会留下幽灵任务）。

用法::

    breaker = CircuitBreaker(failure_rate=0.5, window=20, cooldown=30.0, probe=3)

    # 同步
    @reliable(breaker=breaker, timeout=8.0, max_attempts=5,
              on_open=lambda *a, **k: cache_fallback())
    def call_llm(prompt: str) -> str:
        ...

    # 异步（AsyncOpenAI / AsyncAnthropic / LangGraph 这类客户端）
    @reliable(breaker=breaker, timeout=8.0, max_attempts=5)
    async def acall_llm(prompt: str) -> str:
        ...

历史坑（v0.1.4 及以前，已修）：
- 把 ``async def`` 直接传给同步装饰器，wrapper 只会拿到一个 coroutine
  对象，既没 await 也不抛异常，于是被记成**成功**——熔断器永远看到
  失败率 0.00、永不熔断，而真实异常在装饰器外面才抛出。等于对异步调用
  提供零保护，还谎报健康。现在按函数类型自动分流，并对"同步函数却返回
  协程"显式报错，堵住第二类静默失效。
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import random
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Awaitable, Callable, TypeVar

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
    async_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: Callable[[], float] = random.random,
    on_open: Callable[..., Any] | None = None,
) -> Callable:
    """把函数包上超时 / 重试 / 熔断 / 降级；同步与异步函数都支持。

    - 每次 attempt 前先问熔断器；OPEN 且未到冷却则抛 CircuitOpenError，
      若提供 on_open 则调用其返回值作为降级结果（async 函数允许 async 的
      on_open，会自动 await）。
    - 任何异常（含超时）都记为一次失败并触发退避；成功记为成功。
    - 支持 ``@reliable`` 与 ``@reliable(...)`` 两种写法。
    - ``sleep`` 用于同步退避，``async_sleep`` 用于异步退避（可注入，测试用）。
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    if timeout is not None and timeout <= 0:
        raise ValueError("timeout must be > 0 when provided")

    def decorator(f: F) -> F:
        if inspect.iscoroutinefunction(f):

            @functools.wraps(f)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                last_exc: BaseException | None = None
                for attempt in range(max_attempts):
                    if breaker is not None:
                        try:
                            breaker.before_call()
                        except CircuitOpenError:
                            if on_open is not None:
                                return await _maybe_await(on_open(*args, **kwargs))
                            raise
                    try:
                        result = await _invoke_async(f, args, kwargs, timeout)
                    except Exception as exc:
                        if breaker is not None:
                            breaker.record_failure()
                        last_exc = exc
                        if attempt < max_attempts - 1:
                            await async_sleep(
                                exp_backoff(attempt, base_delay, factor, jitter, rng)
                            )
                    else:
                        if breaker is not None:
                            breaker.record_success()
                        return result
                assert last_exc is not None
                raise last_exc

            return async_wrapper  # type: ignore[return-value]

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


async def _maybe_await(value: Any) -> Any:
    """on_open 既可能是同步回调，也可能是 async 回调。"""
    if inspect.isawaitable(value):
        return await value
    return value


async def _invoke_async(
    fn: Callable[..., Awaitable[Any]],
    args: tuple,
    kwargs: dict,
    timeout: float | None,
) -> Any:
    coro = fn(*args, **kwargs)
    if timeout is None:
        return await coro
    # wait_for 会取消协程 → 真取消，不留幽灵任务
    return await asyncio.wait_for(coro, timeout=timeout)


def _invoke(
    fn: Callable[..., Any],
    args: tuple,
    kwargs: dict,
    timeout: float | None,
) -> Any:
    if timeout is None:
        result = fn(*args, **kwargs)
        _reject_coroutine(result, fn)
        return result
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(fn, *args, **kwargs)
        result = future.result(timeout=timeout)
        _reject_coroutine(result, fn)
        return result
    finally:
        executor.shutdown(wait=False)


def _reject_coroutine(result: Any, fn: Callable[..., Any]) -> None:
    """同步路径拿到协程 → 说明用户把异步函数传错了位置，宁可炸不可静默。

    这是 v0.1.4 之前的真实事故：不报错、按成功记录、熔断器永不触发。
    """
    if inspect.iscoroutine(result):
        result.close()  # 避免 "coroutine was never awaited" 警告
        raise TypeError(
            f"{getattr(fn, '__name__', 'function')} returned a coroutine object. "
            "Pass the `async def` function directly to reliable(); it dispatches "
            "to an async wrapper automatically. Wrapping a sync function that "
            "merely *returns* a coroutine would silently disable retry/breaker."
        )
