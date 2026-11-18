"""Stable, safe errors shared by provider clients and MCP adapters."""

from typing import Literal

ErrorCode = Literal[
    "invalid_input",
    "not_found",
    "timeout",
    "auth",
    "rate_limited",
    "invalid_response",
    "upstream_unavailable",
    "upstream_error",
]


class BridgeError(Exception):
    """Carry a safe summary and machine-readable details without a request or payload."""

    default_source = "bridge"

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode = "invalid_response",
        source: str | None = None,
        status: int | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.source = source or self.default_source
        self.status = status
        self.retry_after_seconds = retry_after_seconds

    @property
    def retryable(self) -> bool:
        return self.code in {"timeout", "rate_limited", "upstream_unavailable"}

    def metadata(self) -> dict[str, str | int | bool]:
        details: dict[str, str | int | bool] = {
            "code": self.code,
            "source": self.source,
            "retryable": self.retryable,
        }
        if self.status is not None:
            details["upstream_status"] = self.status
        if self.retry_after_seconds is not None:
            details["retry_after_seconds"] = self.retry_after_seconds
        return details


def upstream_error_code(status: int) -> ErrorCode:
    match status:
        case 400 | 422:
            return "invalid_input"
        case 401 | 403:
            return "auth"
        case 404:
            return "not_found"
        case 408 | 504:
            return "timeout"
        case 429:
            return "rate_limited"
        case _:
            return "upstream_unavailable" if 500 <= status <= 599 else "upstream_error"
