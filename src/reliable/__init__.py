"""reliable —— 给 LLM/Agent 调用的超时 + 退避 + 熔断，重试风暴防护是一等公民。"""

from .backoff import exp_backoff
from .breaker import CircuitBreaker, State
from .decorator import reliable
from .exceptions import CircuitOpenError, ReliableError

__all__ = [
    "CircuitBreaker",
    "CircuitOpenError",
    "ReliableError",
    "State",
    "exp_backoff",
    "reliable",
]

__version__ = "0.1.0"
