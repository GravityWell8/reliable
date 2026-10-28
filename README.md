# reliable

> Python resilience for LLM/Agent calls — timeout + jitter backoff + sliding-window circuit breaker + fallback. Built to stop retry storms.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org)
[![Status](https://img.shields.io/badge/status-v0.1--alpha-orange.svg)](https://github.com/GravityWell8/reliable)

**给谁**：调 LLM/Agent API 的 Python 开发者。
**解决什么**：超时、限流、偶发 5xx 把 Agent 搞挂；naive retry 引发重试风暴；下游已经瘫了还在持续被打。
**怎么用**：一个装饰器，把任意同步函数包上超时 + 指数退避 + 滑动窗口熔断 + 降级回调。

## 安装

```bash
# 方式一：可编辑安装（推荐，装完任意位置都能 import）
pip install -e .
python3 examples/demo.py
python3 -m unittest discover -s tests

# 方式二：不安装，直接用 PYTHONPATH 指向 src（从仓库根目录运行）
PYTHONPATH=src python3 examples/demo.py
```

> 暂未发布到 PyPI；`pip install reliable` 会装到同名其他包，请勿使用。

## 快速开始

```python
from reliable import CircuitBreaker, reliable

breaker = CircuitBreaker(
    failure_rate=0.5,   # 滑动窗口失败率 >= 50% 即熔断
    window=20,          # 窗口 20 次调用
    cooldown=30.0,      # 熔断后冷却 30 秒
    probe=3,            # 半开后连续成功 3 次才闭合
)

@reliable(
    breaker=breaker,
    timeout=8.0,        # 单次调用超时
    max_attempts=5,     # 含首次共 5 次尝试
    base_delay=0.2,     # 退避基数，factor=2 指数增长
    jitter=0.1,         # ±10% 抖动，防重试同步化
    on_open=lambda *a, **k: cache_fallback(),  # 熔断期间的降级
)
def call_llm(prompt: str) -> str:
    ...
```

跑 demo（完整状态机流转，约 4 秒）：

```bash
python3 examples/demo.py          # 已 pip install -e . 时
# 或 PYTHONPATH=src python3 examples/demo.py
```

跑测试（19 个用例，时钟/sleep 全注入，零真实等待）：

```bash
python3 -m unittest discover -s tests
# 或 PYTHONPATH=src python3 -m unittest discover -s tests
```

## 状态机

```
            失败率 >= 阈值
   CLOSED ────────────────► OPEN
      ▲                       │
      │ probe 次连续成功      │ 冷却到期
      │                       ▼
      └────────────────── HALF_OPEN
                 任一失败 ──► 回到 OPEN
```

- **CLOSED**：正常放行；滑动窗口失败率达到 `failure_rate` → OPEN。
- **OPEN**：直接拒绝（`CircuitOpenError`）或走 `on_open` 降级，不打下游；冷却到期 → HALF_OPEN。
- **HALF_OPEN**：放行探针调用；任一失败 → 回 OPEN（重新计时）；`probe` 次连续成功 → CLOSED 并清空窗口。

## API

| 名称 | 说明 |
|---|---|
| `reliable(fn=None, *, breaker, timeout, max_attempts, base_delay, factor, jitter, sleep, rng, on_open)` | 装饰器 / 包装函数，支持 `@reliable` 与 `@reliable(...)` |
| `CircuitBreaker(failure_rate, window, cooldown, probe, clock, rng)` | 滑动窗口熔断器，线程安全，时钟/随机源可注入 |
| `exp_backoff(attempt, base, factor, jitter, rng)` | 指数退避 + 抖动计算 |
| `CircuitOpenError(retry_after=...)` | 熔断期调用抛出，携带剩余冷却秒数 |

## 设计取舍（为什么和别的 retry 库不一样）

1. **熔断判定看滑动窗口失败率，不看连续失败次数。** 连续 N 次失败的策略对"偶发抖动 + 低流量"过于迟钝、对"突发风暴"过于敏感；窗口失败率对两者都稳。
2. **重试带 ±jitter 抖动。** 无抖动的固定退避会让大量客户端同步重试——重试风暴是这套库防御的第一目标，所以抖动默认开启。
3. **熔断期间走降级回调而不是干等。** `on_open` 让你在下游瘫痪时仍有产出（缓存/简化模型/默认值），错误被转化为降级结果。
4. **HALF_OPEN 不设并发名额闸门。** 名额在探针调用挂死时会死锁在 half_open——这类"idle 探测假阳性/状态卡死"的坑来自真实故障复盘（下游静默卡死、探针挂死），v0.1 用"连续成功 N 次闭合"的简单语义换正确性。

## 已知限制（v0.1）

- `timeout` 语义是"放弃等待"：底层线程仍在运行，生产上请配合 HTTP 客户端自身超时。
- 仅支持同步函数；asyncio 原生版在路线图。

## 路线图

- [ ] **W2**：包成 MCP server，投 mcp.so / Smithery
- [ ] asyncio 原生版（真取消，不放弃等待）
- [ ] TypeScript 版（同 API 语义）
- [ ] 指标导出：每次调用的耗时 / 尝试次数 / 熔断状态变化（可观测性对接）
- [ ] 预算熔断（token / 成本上限，共享同一熔断内核）

## License

MIT
