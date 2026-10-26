"""A1 · reliable 库 demo

展示完整状态机流转：closed -> open -> 降级回调 -> half-open -> closed。

跑法::

    cd reliable && PYTHONPATH=src python3 examples/demo.py
"""

import time

from reliable import CircuitBreaker, reliable

calls = {"n": 0}


def mock_llm(prompt: str) -> str:
    """模拟一个'供应商故障 12 次，然后恢复'的 LLM 端点。

    12 次失败 > window=10，足以把滑动窗口失败率顶过阈值，触发熔断。
    """
    calls["n"] += 1
    if calls["n"] <= 12:
        raise ConnectionError(f"mock provider down (call #{calls['n']})")
    return f"echo: {prompt}"


breaker = CircuitBreaker(
    failure_rate=0.5,  # 窗口失败率 >= 50% 即熔断
    window=10,         # 滑动窗口 10 次调用
    cooldown=1.0,      # 熔断后冷却 1 秒（演示用，生产建议 30s）
    probe=2,           # 半开后连续成功 2 次才闭合
)


def on_open(prompt: str) -> str:
    return f"[fallback] cached answer for {prompt!r}"


protected = reliable(
    mock_llm,
    breaker=breaker,
    timeout=5.0,
    max_attempts=3,
    base_delay=0.05,
    on_open=on_open,
)

for i in range(1, 13):
    if breaker.state.value == "open":
        print(f"call {i:02d}: circuit OPEN, cooling down 1.1s ...")
        time.sleep(1.1)
    try:
        print(f"call {i:02d}: {protected('hi')}")
    except Exception as exc:  # noqa: BLE001 - demo 展示用
        print(f"call {i:02d}: FAILED -> {type(exc).__name__}: {exc}")
    print(f"         breaker = {breaker.state.value}")
