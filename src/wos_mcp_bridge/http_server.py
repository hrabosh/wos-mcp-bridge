"""Authenticated Streamable HTTP for a single provider credential boundary."""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import urlsplit

from fastmcp.server.auth import AccessToken, RemoteAuthProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.http import StarletteWithLifespan
from pydantic import AnyHttpUrl, TypeAdapter, UrlConstraints
from starlette.requests import Request
from starlette.responses import JSONResponse

from .application import Services
from .config import Settings
from .server import create_server

MCP_PATH = "/mcp"
READ_SCOPE = "mcp:read"
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
_ISSUER_URL = TypeAdapter(Annotated[AnyHttpUrl, UrlConstraints(preserve_empty_path=True)])


def _validate_url(name: str, value: str, *, origin: bool = False) -> None:
    try:
        url = urlsplit(value)
        secure = url.scheme == "https" or (
            origin and url.scheme == "http" and url.hostname in LOOPBACK_HOSTS
        )
        if (
            secure
            and url.hostname
            and url.port != 0
            and url.username is None
            and url.password is None
            and "?" not in value
            and "#" not in value
            and all(not char.isspace() and char != "\\" for char in value)
            and (not origin or url.path in {"", "/"})
        ):
            normalized_host = urlsplit(str(AnyHttpUrl(value))).hostname or ""
            if not origin or not any(char in normalized_host for char in "*?[]"):
                return
    except ValueError:
        pass
    requirement = "an HTTPS origin (HTTP is allowed on loopback)" if origin else "an HTTPS URL"
    raise ValueError(f"{name} must be {requirement}.") from None


@dataclass(frozen=True)
class HTTPSettings:
    origin: str
    issuer: str
    jwks_uri: str

    def __post_init__(self) -> None:
        _validate_url("WOS_MCP_HTTP_ORIGIN", self.origin, origin=True)
        _validate_url("WOS_MCP_AUTH_ISSUER", self.issuer)
        _validate_url("WOS_MCP_AUTH_JWKS_URI", self.jwks_uri)
        try:
            issuer = _ISSUER_URL.validate_python(self.issuer)
        except ValueError:
            raise ValueError("WOS_MCP_AUTH_ISSUER must be a valid HTTPS URL.") from None
        if str(issuer) != self.issuer:
            raise ValueError("WOS_MCP_AUTH_ISSUER must be a canonical HTTPS URL.")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> HTTPSettings:
        values = os.environ if environ is None else environ
        return cls(
            origin=values.get("WOS_MCP_HTTP_ORIGIN", ""),
            issuer=values.get("WOS_MCP_AUTH_ISSUER", ""),
            jwks_uri=values.get("WOS_MCP_AUTH_JWKS_URI", ""),
        )


class _AccessTokenVerifier(JWTVerifier):
    async def verify_token(self, token: str) -> AccessToken | None:
        verified = await super().verify_token(token)
        if verified is None or verified.expires_at is None:
            return None
        not_before = (verified.claims or {}).get("nbf")
        if not_before is not None and (
            not isinstance(not_before, (int, float)) or not not_before <= time.time()
        ):
            return None
        return verified


def create_http_app(
    http: HTTPSettings | None = None,
    *,
    settings: Settings | None = None,
    services: Services | None = None,
) -> StarletteWithLifespan:
    """ASGI factory. Every MCP request requires a token issued for this resource."""
    http = http if http is not None else HTTPSettings.from_env()
    origin = str(AnyHttpUrl(http.origin)).rstrip("/")
    verifier = _AccessTokenVerifier(
        issuer=http.issuer,
        jwks_uri=http.jwks_uri,
        audience=f"{origin}{MCP_PATH}",
        algorithm="RS256",
    )
    auth = RemoteAuthProvider(
        token_verifier=verifier,
        authorization_servers=[_ISSUER_URL.validate_python(http.issuer)],
        base_url=origin,
        scopes_supported=[READ_SCOPE],
        challenge_scopes=[READ_SCOPE],
    )
    # Let FastMCP distinguish invalid tokens from valid tokens with insufficient scope.
    auth.required_scopes.append(READ_SCOPE)
    server = create_server(
        settings,
        services=services,
        auth=auth,
    )

    @server.custom_route("/healthz", methods=["GET"])
    async def health(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    return server.http_app(
        path=MCP_PATH,
        transport="streamable-http",
        json_response=False,
        stateless_http=False,
        host_origin_protection=True,
        allowed_hosts=[urlsplit(origin).netloc],
        allowed_origins=[origin],
    )
