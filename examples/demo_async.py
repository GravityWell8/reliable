"""A1 · reliable 异步 demo

展示 async 路径的两个关键点：
1. 超时是**真取消**——协程收到 CancelledError，不留幽灵任务；
2. 熔断器看到的是真实失败（v0.1.4 及以前对 async 调用会记成"成功"，
   失败率永远 0.00、永不熔断）。

跑法::

    cd reliable && PYTHONPATH=src python3 examples/demo_async.py
"""

import asyncio

from reliable import CircuitBreaker, reliable

attempts = {"n": 0}


async def mock_provider(prompt: str) -> str:
    """模拟'连续失败 6 次后恢复'的异步 LLM 端点。"""
    attempts["n"] += 1
    await asyncio.sleep(0.01)  # 模拟网络 IO
    if attempts["n"] <= 6:
        raise ConnectionError(f"provider 503 (attempt #{attempts['n']})")
    return f"echo: {prompt}"


async def slow_provider(prompt: str) -> str:
    """永远超慢，用来演示真取消。"""
    await asyncio.sleep(10)
    return "never reached"


breaker = CircuitBreaker(
    failure_rate=0.5,
    window=4,
    cooldown=0.2,   # 演示用
    probe=1,
)


async def main() -> None:
    guarded = reliable(
        mock_provider,
        breaker=breaker,
        timeout=1.0,
        max_attempts=2,
        base_delay=0.01,
    )

    print("=== 1) 失败重试 + 熔断 ===")
    for i in range(1, 7):
        try:
            out = await guarded("hi")
            print(f"call {i}: OK -> {out}")
        except Exception as exc:  # noqa: BLE001 - demo
            print(f"call {i}: FAILED -> {type(exc).__name__}: {exc}")
        s = breaker.stats()
        print(
            f"        breaker={s['state']:9s} "
            f"window={s['window_total']} failures={s['window_failures']} "
            f"rate={s['window_failure_rate']:.2f}"
        )

    print()
    print("=== 2) 超时 = 真取消（协程收到 CancelledError）===")
    cancelled = {"v": False}

    async def cancellable(prompt: str) -> str:
        nonlocal cancelled
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled["v"] = True
            print("        ^ 协程已被取消，不会在后台继续跑")
            raise
        return "done"

    timed = reliable(cancellable, timeout=0.05, max_attempts=1)
    try:
        await timed("hi")
    except TimeoutError:
        print("call: TIMEOUT (as expected)")
    print(f"        cancelled={cancelled['v']}")


if __name__ == "__main__":
    asyncio.run(main())
