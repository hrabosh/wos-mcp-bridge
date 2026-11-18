"""Operation-local counters and timings for fixed provider names and phases."""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter

_provider_metrics: ContextVar[dict[str, dict[str, int]] | None] = ContextVar(
    "provider_metrics", default=None
)


@contextmanager
def collect_provider_metrics() -> Generator[dict[str, dict[str, int]]]:
    metrics: dict[str, dict[str, int]] = {}
    token = _provider_metrics.set(metrics)
    try:
        yield metrics
    finally:
        _provider_metrics.reset(token)


def record_metric(source: str, name: str, amount: int = 1) -> None:
    if (metrics := _provider_metrics.get()) is not None:
        provider = metrics.setdefault(source, {})
        provider[name] = provider.get(name, 0) + amount


@contextmanager
def measure(source: str, name: str) -> Generator[None]:
    start = perf_counter()
    try:
        yield
    finally:
        record_metric(source, name, round((perf_counter() - start) * 1000))
