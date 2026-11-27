# reliable

> Python resilience for LLM/Agent calls — timeout + jitter backoff + sliding-window circuit breaker + fallback. Sync and async. Built to stop retry storms.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org)
[![Status](https://img.shields.io/badge/status-v0.1.5-blue.svg)](https://github.com/GravityWell8/reliable)
<!-- mcp-name: io.github.GravityWell8/reliable -->

**给谁**：调 LLM/Agent API 的 Python 开发者。
**解决什么**：超时、限流、偶发 5xx 把 Agent 搞挂；naive retry 引发重试风暴；下游已经瘫了还在持续被打。
**怎么用**：一个装饰器，把 `def` 或 `async def` 函数包上超时 + 指数退避 + 滑动窗口熔断 + 降级回调。

## 安装

```bash
# 从 PyPI 安装（发布名 reliablekit，import 名仍为 reliable）
pip install reliablekit

# 本地开发：可编辑安装（推荐）
pip install -e .
python3 examples/demo.py
python3 -m pytest -q          # 或 python3 -m unittest discover -s tests

# 不安装，直接用 PYTHONPATH 指向 src（从仓库根目录运行）
PYTHONPATH=src python3 examples/demo.py
```

> 发布名是 `reliablekit`（PyPI 不允许 `reliable`，因与已存在的 `reliability` 太像被防 typosquat 规则拦截）；导入时仍用 `import reliable`。

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

异步函数同一个装饰器，自动分流（`AsyncOpenAI` / `AsyncAnthropic` / LangGraph 这类客户端）：

```python
@reliable(breaker=breaker, timeout=8.0, max_attempts=5)
async def acall_llm(prompt: str) -> str:
    ...   # 超时走 asyncio.wait_for，是"真取消"，不会留幽灵任务

print(await acall_llm("hi"))
```

> **为什么 async 单列一节**：0.1.4 及以前，`async def` 被同步装饰器包住时只会拿到一个 coroutine 对象，既不 `await` 也不抛异常 → 被记成**成功** → 熔断器永远看到失败率 `0.00`、永不熔断。等于对异步调用零保护还谎报健康。0.1.5 起按函数类型自动分流，并对"同步函数却返回协程"显式报 `TypeError`，堵住第二类静默失效。

跑 demo：

```bash
python3 examples/demo.py          # 同步：完整状态机流转（closed→open→降级→half-open→closed）
python3 examples/demo_async.py    # 异步：重试 + 熔断 + 超时真取消
# 或 PYTHONPATH=src python3 examples/demo_async.py
```

跑测试（39 个用例，时钟/sleep 全注入，零真实等待）：

```bash
python3 -m pytest -q
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
| `reliable(fn=None, *, breaker, timeout, max_attempts, base_delay, factor, jitter, sleep, async_sleep, rng, on_open)` | 装饰器 / 包装函数；自动识别 sync / async，支持 `@reliable` 与 `@reliable(...)` |
| `CircuitBreaker(failure_rate, window, cooldown, probe, clock, rng)` | 滑动窗口熔断器，线程安全，时钟/随机源可注入 |
| `exp_backoff(attempt, base, factor, jitter, rng)` | 指数退避 + 抖动计算 |
| `CircuitOpenError(retry_after=...)` | 熔断期调用抛出，携带剩余冷却秒数 |

## 设计取舍（为什么和别的 retry 库不一样）

1. **熔断判定看滑动窗口失败率，不看连续失败次数。** 连续 N 次失败的策略对"偶发抖动 + 低流量"过于迟钝、对"突发风暴"过于敏感；窗口失败率对两者都稳。
2. **重试带 ±jitter 抖动。** 无抖动的固定退避会让大量客户端同步重试——重试风暴是这套库防御的第一目标，所以抖动默认开启。
3. **熔断期间走降级回调而不是干等。** `on_open` 让你在下游瘫痪时仍有产出（缓存/简化模型/默认值），错误被转化为降级结果。
4. **HALF_OPEN 不设并发名额闸门。** 名额在探针调用挂死时会死锁在 half_open——这类"idle 探测假阳性/状态卡死"的坑来自真实故障复盘（下游静默卡死、探针挂死），v0.1 用"连续成功 N 次闭合"的简单语义换正确性。

## 已知限制

- **同步函数的 `timeout` 是"放弃等待"**：底层线程仍在运行，生产上请配合 HTTP 客户端自身超时。
- **异步函数的 `timeout` 是真取消**（`asyncio.wait_for` 会向协程抛 `CancelledError`），语义更干净。
- `asyncio.CancelledError` 不会被当成失败重试——外层任务被取消时直接向上传播，这是刻意的。

## 路线图

- [x] MCP server（已发布，见下）
- [x] asyncio 原生版（真取消，不放弃等待）
- [ ] TypeScript 版（同 API 语义）
- [ ] 指标导出：每次调用的耗时 / 尝试次数 / 熔断状态变化（可观测性对接）
- [ ] 预算熔断（token / 成本上限，共享同一熔断内核）

## MCP Server

把库里的容错原语暴露成 LLM Agent 可直接调用的 MCP 工具，适合"Agent 要调很多 LLM/HTTP 下游、又不想自己重写熔断"的场景。

**提供的 surface：**
- 工具 `create_breaker`：给每个下游依赖（如某 LLM provider）建一个命名熔断器。
- 工具 `reliable_http_call`：通过命名熔断器发起带超时 / 退避 / 降级的 HTTP 调用；熔断 OPEN 时直接短路返回 `fallback`，不再打下游（重试风暴防护）。
- 工具 `get_breaker_state` / `compute_backoff`：查状态、预览退避节奏。
- 资源 `reliable://breaker/{name}`：熔断器的实时 stats。
- 提示 `diagnose_breaker`：根据状态生成健康诊断 prompt。

**接入（stdio，供 Claude Desktop / Cursor / Codex 等 MCP 客户端）：**

官方 MCP Registry 名：`io.github.GravityWell8/reliable`。装好后一条命令即可运行（无需 clone 仓库）：

```bash
uvx reliablekit          # 或 pip install reliablekit && reliablekit
```

粘进客户端配置（Claude Desktop / Cursor 通用）：

```json
{
  "mcpServers": {
    "reliable": {
      "command": "uvx",
      "args": ["reliablekit"]
    }
  }
}
```

命令行客户端：

```bash
claude mcp add --transport stdio reliable -- uvx reliablekit
```

**每个工具都声明了 `outputSchema`**（返回结构 + 分支字段），Agent 不需要猜返回格式，也少了工具投毒面。

**Manifest 文件（仓库根，供注册表提交 / 自托管）：** `mcp.json`、`.mcp.json`，以及可复用的 `smithery.yaml` + `Dockerfile`（供需要容器构建的注册表）。

> 状态保存在 server 进程内的 `_BREAKERS` 字典（单实例、线程安全由 `CircuitBreaker` 保证）。多副本需要共享状态时，把 `_BREAKERS` 换成 Redis/etcd 后端即可——这点与"可插拔存储后端"的设计原则同构。

## License

MIT
