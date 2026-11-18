"""Explicit immutable application settings, with secrets excluded from repr."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from math import isfinite
from typing import Literal

Profile = Literal["full", "literature"]


def read_profile(values: Mapping[str, str]) -> Profile:
    profile = values.get("WOS_MCP_PROFILE", "full")
    if profile == "full" or profile == "literature":
        return profile
    raise ValueError("WOS_MCP_PROFILE must be full or literature.")


def _seconds(values: Mapping[str, str], name: str, default: float) -> float:
    try:
        return float(values[name]) if name in values else default
    except ValueError:
        raise ValueError(f"{name} must be a number of seconds.") from None


@dataclass(frozen=True)
class Settings:
    wos_api_key: str = field(default="", repr=False)
    openalex_api_key: str = field(default="", repr=False)
    min_interval: float = 1.1
    cache_ttl: float = 900
    operation_timeout: float = 30
    profile: Profile = "full"

    def __post_init__(self) -> None:
        read_profile({"WOS_MCP_PROFILE": self.profile})
        for name, value in (
            ("WOS_MCP_MIN_INTERVAL_SECONDS", self.min_interval),
            ("WOS_MCP_CACHE_TTL_SECONDS", self.cache_ttl),
            ("WOS_MCP_OPERATION_TIMEOUT_SECONDS", self.operation_timeout),
        ):
            if not isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative.")
        if self.operation_timeout == 0:
            raise ValueError("WOS_MCP_OPERATION_TIMEOUT_SECONDS must be greater than zero.")

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None, *, profile: Profile | None = None
    ) -> Settings:
        values = os.environ if environ is None else environ
        return cls(
            wos_api_key=values.get("WOS_API_KEY", ""),
            openalex_api_key=values.get("OPENALEX_API_KEY", ""),
            min_interval=_seconds(values, "WOS_MCP_MIN_INTERVAL_SECONDS", 1.1),
            cache_ttl=_seconds(values, "WOS_MCP_CACHE_TTL_SECONDS", 900),
            operation_timeout=_seconds(values, "WOS_MCP_OPERATION_TIMEOUT_SECONDS", 30),
            profile=read_profile(values) if profile is None else profile,
        )
