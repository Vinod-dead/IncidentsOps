"""Tool-call resilience: timeout, retry w/ exponential backoff + jitter, circuit breaker."""

import random
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any

from .config import Settings

_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="tool")


class ToolError(Exception):
    def __init__(self, message: str, transient: bool = True) -> None:
        super().__init__(message)
        self.transient = transient


class ToolTimeout(ToolError):
    pass


class CircuitOpenError(ToolError):
    def __init__(self, message: str) -> None:
        super().__init__(message, transient=False)


class SimulatedCrash(RuntimeError):
    """Raised by the crash-injection hook to test checkpoint recovery."""


class CircuitBreaker:
    """closed -> (N consecutive failures) -> open -> (cooldown) -> half_open -> closed|open."""

    def __init__(
        self, threshold: int, cooldown_s: float, clock: Callable[[], float] = time.monotonic
    ):
        self.threshold, self.cooldown_s, self._clock = threshold, cooldown_s, clock
        self.failures = 0
        self.opened_at: float | None = None

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        if self._clock() - self.opened_at >= self.cooldown_s:
            return "half_open"
        return "open"

    def allow(self) -> bool:
        return self.state != "open"

    def record_success(self) -> None:
        self.failures, self.opened_at = 0, None

    def record_failure(self) -> None:
        self.failures += 1
        if self.state == "half_open" or self.failures >= self.threshold:
            self.opened_at = self._clock()


class Resilient:
    """Wraps tool calls. One breaker per tool name, in-process."""

    def __init__(
        self,
        settings: Settings,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
    ) -> None:
        self.s, self._sleep, self._clock = settings, sleep, clock
        self._rng = rng or random.Random(0)
        self.breakers: dict[str, CircuitBreaker] = {}
        self.stats = {"retries": 0, "timeouts": 0, "breaker_rejections": 0}

    def breaker(self, name: str) -> CircuitBreaker:
        if name not in self.breakers:
            self.breakers[name] = CircuitBreaker(
                self.s.breaker_threshold, self.s.breaker_cooldown_s, self._clock
            )
        return self.breakers[name]

    def _delay(self, attempt: int) -> float:
        cap = min(self.s.backoff_max_s, self.s.backoff_base_s * 2 ** (attempt - 1))
        return cap * (0.5 + self._rng.random() / 2)  # "equal jitter"

    def call(self, name: str, fn: Callable[..., Any], **kwargs: Any) -> Any:
        br = self.breaker(name)
        attempt = 0
        while True:
            if not br.allow():
                self.stats["breaker_rejections"] += 1
                raise CircuitOpenError(f"circuit open for tool '{name}'")
            attempt += 1
            try:
                fut = _POOL.submit(fn, **kwargs)
                result = fut.result(timeout=self.s.tool_timeout_s)
            except TypeError as e:  # bad arguments from the LLM: not the tool's fault
                raise ToolError(f"bad arguments for {name}: {e}", transient=False) from e
            except FutureTimeout:
                self.stats["timeouts"] += 1
                br.record_failure()
                err: ToolError = ToolTimeout(f"{name} timed out after {self.s.tool_timeout_s}s")
            except ToolError as e:
                br.record_failure()
                err = e
            except Exception as e:  # noqa: BLE001 - unknown tool failure, not retried
                br.record_failure()
                raise ToolError(f"{name} failed: {e}", transient=False) from e
            else:
                br.record_success()
                return result
            if not err.transient or attempt >= self.s.tool_max_attempts:
                raise err
            self.stats["retries"] += 1
            self._sleep(self._delay(attempt))
