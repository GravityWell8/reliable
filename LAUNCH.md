# Reliable — 发布与首发文案

> 用途：W2 冷启动。把 `reliable` 推到 mcp.so / Smithery，并在 Twitter/X + HN 首发。
> 差异点（所有文案统一锚定这一句）：**"给 Agent 的每次下游调用默认带上重试风暴防护，而不是裸 retry。"**

---

## 一、Twitter / X 线程（首发）

**主推文（≤280 字）：**
```
Shipped `reliable` — a tiny Python lib + MCP server that wraps every
LLM/Agent downstream call with timeout + jitter backoff + sliding-window
circuit breaker + fallback.

The point: stop retry storms by default, not after your provider melts.

github.com/GravityWell8/reliable
```

**追推 1（设计取舍钩子，给工程师）：**
```
Most retry libs are naive `for i in range(3)`. When the provider is already
down, that *is* the outage — every client hammers it in lockstep.

reliable trips on sliding-window failure rate, not "N consecutive fails",
and opens with ±jitter so retries don't sync up. Same bug class that takes
down real training clusters, just smaller.
```

**追推 2（MCP 入口）：**
```
It also ships as an MCP server. An Agent can spin up a named breaker per
provider and call through it — breaker OPEN short-circuits to fallback
instead of hammering the downstream.

pip install -e ".[mcp]" && python -m reliable.mcp
```

---

## 二、Hacker News — Show HN

**标题：**
```
Show HN: Reliable – circuit breakers and retry-storm guard for LLM/Agent calls
```

**正文：**
```
reliable is a small Python library (+ MCP server) that wraps any LLM/Agent
downstream call with:

- timeout + exponential backoff with ±jitter (default on, to stop retry sync)
- a sliding-window circuit breaker (trips on failure *rate*, not "N consecutive
  fails") with OPEN / HALF_OPEN / CLOSED states
- a fallback callback so a down dependency still yields output

The motivation: naive `for i in range(3)` retries turn a single provider blip
into a retry storm — every client hammers the same endpoint in lockstep. The
breaker's whole job is to stop that by default.

It's zero-dependency for the core lib, thread-safe, and the breaker logic is
directly transplantable from a real fault-tolerance framework (the design
tradeoffs section in the README spells out why I avoided a HALF_OPEN
concurrency gate).

Repo: https://github.com/GravityWell8/reliable
```

---

## 三、发布检查清单（按顺序）

- [ ] **GitHub Topics**（Settings → Topics）：`mcp-server` `model-context-protocol` `python` `llm` `circuit-breaker` `retry` `resilience` `agent`
- [ ] **mcp.so**：打开 https://mcp.so → Add Server → 粘贴 `https://github.com/GravityWell8/reliable` → 等待审核（社区目录，手动）
- [ ] **Smithery**：打开 https://smithery.ai → Submit server → 粘贴同一仓库 URL。仓库根已有 `smithery.yaml` + `.mcp.json` + `Dockerfile`，扫描器会自动校验
- [ ] 发 Twitter/X 线程（主推 + 2 追推，间隔 ~1 小时）
- [ ] 发 HN Show HN（美西工作日上午发，曝光最好）
- [ ] 回复前 24h 的评论，尤其关于"和 tenacity/pybreaker 有什么区别"——答案就是 README 设计取舍那四节

---

## 四、常见质疑与口径

**Q: 和 tenacity / pybreaker 有什么区别？**
A: tenacity 是装饰器重试，没有熔断；pybreaker 有熔断但看连续失败次数、无 jitter、无 LLM 场景默认。reliable 把"滑动窗口失败率 + 默认抖动 + OPEN 降级"三者作为出厂默认值，专门针对 Agent 调下游的重试风暴。

**Q: 为什么 HALF_OPEN 不设并发名额？**
A: 名额在探针挂死时会卡死在 half_open（这是真实故障复盘里的坑）。v0.1 用"连续成功 N 次闭合"换正确性，详见 README 设计取舍第 4 条。
