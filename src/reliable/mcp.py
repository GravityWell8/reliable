"""reliable 的 MCP server。

把库里的容错原语暴露成 LLM Agent 可直接调用的工具：

- 给每个下游依赖（如某个 LLM provider）建一个命名熔断器；
- 通过熔断器发起带超时/退避/降级的 HTTP 调用；
- 查询熔断状态、预览退避节奏。

运行（stdio，供 Claude / Cursor 等 MCP 客户端接入）::

    pip install reliablekit
    reliablekit
    # 或 python -m reliable.mcp

设计取舍：server 内用进程内字典保存命名熔断器（单实例、线程安全由
CircuitBreaker 自身保证）。多副本场景需要共享状态时，把 _BREAKERS 换成
Redis/etcd 后端即可——这点与"可插拔存储后端"的设计原则同构。

所有工具都声明了返回类型（TypedDict），因此 MCP 协议里带有完整的
outputSchema，Agent 不必猜返回结构。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, TypedDict

from mcp.server.mcpserver import MCPServer

from . import CircuitBreaker, CircuitOpenError, exp_backoff, reliable

mcp = MCPServer("reliable")

# 进程内命名熔断器注册表：name -> CircuitBreaker
_BREAKERS: dict[str, CircuitBreaker] = {}


class _Stats(TypedDict, total=False):
    """CircuitBreaker.stats() 的字段集合（错误分支下可能缺失）。"""

    state: str
    failure_rate_threshold: float
    window: int
    window_total: int
    window_failures: int
    window_failure_rate: float
    cooldown: float
    probe: int
    opened_at: float | None


class _Named(TypedDict):
    name: str


class _OptionalError(TypedDict, total=False):
    error: str


class CreateBreakerResult(_Stats):
    """create_breaker 的返回结构。"""

    name: str
    created: bool


class BreakerStateResult(_Stats, _Named, _OptionalError):
    """get_breaker_state 的返回结构（未知 name 时只返回 name + error）。"""


class BackoffResult(TypedDict):
    """compute_backoff 的返回结构。"""

    attempt: int
    delay: float


class _HttpCore(TypedDict):
    ok: bool
    breaker: str


class _HttpBranch(TypedDict, total=False):
    state: str
    body: str
    open: bool
    retry_after: float
    fallback: str | None
    http_status: int
    error: str


class HttpCallResult(_HttpCore, _HttpBranch):
    """reliable_http_call 的返回结构。

    只有 ok/breaker 恒定存在；其余字段按分支出现：
    - 成功：state、body
    - 熔断 OPEN：open、retry_after、fallback
    - HTTP 非 2xx：state、http_status、error
    - 其它异常：state、error
    """


def _get_breaker(name: str) -> CircuitBreaker | None:
    return _BREAKERS.get(name)


def _parse_headers(headers: str | None) -> dict[str, str]:
    if not headers:
        return {}
    return json.loads(headers)


def _encode(body: str | None) -> bytes | None:
    return body.encode("utf-8") if body is not None else None


def _http(method: str, url: str, headers: dict, payload: bytes | None, timeout: float) -> str:
    req = urllib.request.Request(
        url, data=payload, method=method.upper(), headers=headers
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


@mcp.tool()
def create_breaker(
    name: str,
    failure_rate: float = 0.5,
    window: int = 20,
    cooldown: float = 30.0,
    probe: int = 3,
) -> CreateBreakerResult:
    """Register or replace a named circuit breaker.

    Use one breaker per downstream dependency (e.g. one per LLM provider).
    The breaker lives in the server process for its lifetime.

    Args:
        name: unique breaker id, e.g. "openai", "local-llm".
        failure_rate: trip when the sliding-window failure rate >= this (0..1).
        window: number of recent calls in the sliding window.
        cooldown: seconds the breaker stays OPEN before probing.
        probe: consecutive HALF_OPEN successes required to close.

    Example:
        create_breaker("openai", failure_rate=0.5, window=20)
        -> {"name": "openai", "created": true, "state": "closed",
            "failure_rate_threshold": 0.5, "window": 20, "window_total": 0,
            "window_failures": 0, "window_failure_rate": 0.0,
            "cooldown": 30.0, "probe": 3, "opened_at": null}
    """
    _BREAKERS[name] = CircuitBreaker(
        failure_rate=failure_rate, window=window, cooldown=cooldown, probe=probe
    )
    return {"name": name, "created": True, **_BREAKERS[name].stats()}


@mcp.tool()
def get_breaker_state(name: str) -> BreakerStateResult:
    """Return the current state and stats of a named breaker.

    Args:
        name: breaker id previously created with create_breaker.

    Returns state (closed/open/half_open), failure-rate threshold, window
    stats, cooldown and probe config.

    Example:
        get_breaker_state("openai")
        -> {"name": "openai", "state": "open", "window_failure_rate": 0.8,
            "window_total": 20, "window_failures": 16, "opened_at": 1731000000.0, ...}
        get_breaker_state("nope")
        -> {"name": "nope", "error": "unknown breaker: nope"}
    """
    b = _get_breaker(name)
    if b is None:
        return {"name": name, "error": f"unknown breaker: {name}"}
    return {"name": name, **b.stats()}


@mcp.tool()
def compute_backoff(
    attempt: int,
    base_delay: float = 0.2,
    factor: float = 2.0,
    jitter: float = 0.1,
) -> BackoffResult:
    """Compute the exponential backoff delay for a retry attempt (pure).

    No side effects — handy to preview retry pacing before committing config.

    Args:
        attempt: 1-based retry index.
        base_delay: initial delay in seconds.
        factor: growth multiplier per attempt.
        jitter: +/- fraction of randomness (0 disables it).

    Example:
        compute_backoff(3, base_delay=0.2, factor=2.0, jitter=0.0)
        -> {"attempt": 3, "delay": 0.8}
        compute_backoff(5, base_delay=0.5, factor=2.0, jitter=0.0)
        -> {"attempt": 5, "delay": 8.0}
    """
    delay = exp_backoff(attempt, base_delay, factor, jitter)
    return {"attempt": attempt, "delay": delay}


@mcp.tool()
def reliable_http_call(
    breaker: str,
    url: str,
    method: str = "POST",
    headers: str | None = None,
    body: str | None = None,
    timeout: float = 8.0,
    max_attempts: int = 5,
    base_delay: float = 0.2,
    factor: float = 2.0,
    jitter: float = 0.1,
    fallback: str | None = None,
) -> HttpCallResult:
    """Call an HTTP endpoint through a named circuit breaker.

    The breaker is auto-created with default config if the name is new.
    On breaker OPEN the call short-circuits and returns `fallback` (or an
    error) instead of hammering the downstream — that is the retry-storm
    guard this library exists for.

    Args:
        breaker: breaker id (auto-created if absent).
        url: target URL.
        method: HTTP method (default POST).
        headers: JSON string of request headers.
        body: request body as string/JSON.
        timeout: per-attempt timeout in seconds.
        max_attempts: total tries including the first.
        base_delay / factor / jitter: retry backoff config.
        fallback: JSON string returned when the call cannot succeed.

    Example:
        reliable_http_call("openai", "https://api.example.com/v1/chat",
                           body='{"q": "hi"}', max_attempts=3)
        -> {"ok": true, "breaker": "openai", "state": "closed", "body": "..."}

        # downstream already tripped: no request is made at all
        reliable_http_call("openai", "https://api.example.com/v1/chat",
                           fallback='{"cached": true}')
        -> {"ok": false, "breaker": "openai", "open": true,
            "retry_after": 12.4, "fallback": "{\\"cached\\": true}"}
    """
    b = _get_breaker(breaker) or _BREAKERS.setdefault(breaker, CircuitBreaker())

    try:
        result = reliable(
            breaker=b,
            timeout=timeout,
            max_attempts=max_attempts,
            base_delay=base_delay,
            factor=factor,
            jitter=jitter,
        )(_http)(method, url, _parse_headers(headers), _encode(body), timeout)
        return {"ok": True, "breaker": breaker, "state": b.state.value, "body": result}
    except CircuitOpenError as e:
        return {
            "ok": False,
            "breaker": breaker,
            "open": True,
            "retry_after": e.retry_after,
            "fallback": fallback,
        }
    except urllib.error.HTTPError as e:  # non-2xx
        return {
            "ok": False,
            "breaker": breaker,
            "state": b.state.value,
            "http_status": e.code,
            "error": e.reason,
        }
    except Exception as e:  # noqa: BLE001 - surface everything else as error
        return {"ok": False, "breaker": breaker, "state": b.state.value, "error": str(e)}


@mcp.resource("reliable://breaker/{name}")
def breaker_resource(name: str) -> dict[str, Any]:
    """Expose a breaker's live stats as an MCP resource."""
    b = _get_breaker(name)
    if b is None:
        return {"error": f"unknown breaker: {name}"}
    return b.stats()


@mcp.prompt()
def diagnose_breaker(name: str) -> str:
    """Build a diagnostic prompt for a named breaker's current health."""
    b = _get_breaker(name)
    if b is None:
        return (
            f"Breaker '{name}' does not exist yet. "
            "Create it first with the create_breaker tool."
        )
    s = b.stats()
    rate = s["window_failure_rate"]
    thr = s["failure_rate_threshold"]
    return (
        f"Analyze the circuit breaker '{name}'.\n"
        f"Current state: {s['state']}.\n"
        f"Window failure rate: {rate:.0%} (trip threshold {thr:.0%}), "
        f"window size {s['window_total']}/{s['window']}.\n"
        f"Cooldown {s['cooldown']}s, probe {s['probe']}.\n"
        "Is the downstream dependency healthy? Should we tighten or loosen "
        "the threshold, and what action do you recommend next?"
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
