"""Explicit immutable application settings, with secrets excluded from repr."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    wos_api_key: str = field(default="", repr=False)
    openalex_api_key: str = field(default="", repr=False)
    min_interval: float = 1.1
    cache_ttl: float = 900
    operation_timeout: float = 30

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        values = os.environ if environ is None else environ
        return cls(
            wos_api_key=values.get("WOS_API_KEY", ""),
            openalex_api_key=values.get("OPENALEX_API_KEY", ""),
        )
