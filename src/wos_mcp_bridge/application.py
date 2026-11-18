"""Compose concrete services and own resources for one credential boundary."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

from .config import Settings
from .europe_pmc import EuropePmcClient
from .literature_client import LiteratureClient
from .openalex_client import OpenAlexClient
from .wos_client import WosClient


@dataclass(frozen=True)
class Services:
    wos: WosClient
    literature: LiteratureClient


@asynccontextmanager
async def open_services(settings: Settings) -> AsyncIterator[Services]:
    """Register cleanup immediately, including failures partway through startup."""
    async with AsyncExitStack() as resources:
        wos = WosClient(
            settings.wos_api_key,
            min_interval=settings.min_interval,
            cache_ttl=settings.cache_ttl,
            operation_timeout=settings.operation_timeout,
        )
        resources.push_async_callback(wos.aclose)
        europe_pmc = EuropePmcClient(
            min_interval=settings.min_interval,
            cache_ttl=settings.cache_ttl,
            operation_timeout=settings.operation_timeout,
        )
        resources.push_async_callback(europe_pmc.aclose)
        openalex = OpenAlexClient(
            settings.openalex_api_key,
            min_interval=settings.min_interval,
            cache_ttl=settings.cache_ttl,
            operation_timeout=settings.operation_timeout,
        )
        resources.push_async_callback(openalex.aclose)
        yield Services(wos=wos, literature=LiteratureClient(europe_pmc, openalex))
