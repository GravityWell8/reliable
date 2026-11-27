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

__version__ = "0.1.5"
try:  # 让版本号跟随已安装的分发包，避免和 pyproject 漂移
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _dist_version

    try:
        __version__ = _dist_version("reliablekit")
    except PackageNotFoundError:  # 源码目录直接运行时没有元数据
        pass
except Exception:  # pragma: no cover - 极老环境
    pass
