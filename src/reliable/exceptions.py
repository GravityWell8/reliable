"""reliable 的异常定义。"""

from __future__ import annotations


class ReliableError(Exception):
    """reliable 库的异常基类。"""


class CircuitOpenError(ReliableError):
    """熔断器处于 OPEN（或半开名额已拒绝）时发起调用。

    携带剩余冷却秒数，调用方可据此决定等待或直接降级。
    """

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after
