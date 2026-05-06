"""Credential injection — tools never see plaintext secrets.

Credentials are resolved and injected at the HTTP request layer,
keeping tool code free of secret material.

Injection methods:
  - Bearer header: Authorization: Bearer <secret>
  - Basic auth: Authorization: Basic base64(user:pass)
  - Custom header: X-API-Key: <secret>
  - Query parameter: ?key=<secret>

Reference: ironclaw/src/tools/wasm/credential_injector.rs
"""

from __future__ import annotations

import base64
import fnmatch
from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from src.security.secret_refs import resolve_inline_mapping_refs, resolve_inline_secret_refs


class InjectionMethod(Enum):
    BEARER = "bearer"
    BASIC = "basic"
    HEADER = "header"
    QUERY = "query"


@dataclass
class CredentialMapping:
    """Maps a secret to an injection point."""

    secret_name: str
    method: InjectionMethod
    host_patterns: list[str]  # e.g. ["api.openai.com", "*.anthropic.com"]
    header_name: str = ""  # For HEADER method
    query_param: str = ""  # For QUERY method
    username: str = ""  # For BASIC method


@dataclass
class CredentialRegistry:
    """Thread-safe registry of credential mappings."""

    mappings: list[CredentialMapping] = field(default_factory=list)
    allowed_secrets: set[str] = field(default_factory=set)

    def add_mapping(self, mapping: CredentialMapping) -> None:
        self.mappings.append(mapping)
        self.allowed_secrets.add(mapping.secret_name)

    def find_for_host(self, host: str) -> list[CredentialMapping]:
        """Find all credential mappings matching *host*."""
        return [m for m in self.mappings if _host_matches(host, m.host_patterns)]


class CredentialInjector:
    """Inject credentials into HTTP requests without exposing secrets to tools."""

    def __init__(
        self,
        registry: CredentialRegistry,
        secret_resolver: "SecretResolver",
    ) -> None:
        self._registry = registry
        self._resolver = secret_resolver

    def inject_headers(
        self,
        host: str,
        headers: dict[str, str],
        query_params: dict[str, str] | None = None,
    ) -> tuple[dict[str, str], dict[str, str] | None]:
        """Inject credentials into *headers* (and optionally *query_params*).

        Returns the modified (headers, query_params) tuple.
        Only injects secrets that are in the registry's allowed_secrets set.
        """
        mappings = self._registry.find_for_host(host)
        if not mappings:
            return headers, query_params

        headers = dict(headers)  # Don't mutate caller's dict
        if query_params is not None:
            query_params = dict(query_params)

        for mapping in mappings:
            query_params = self._inject_mapping(mapping, headers, query_params)

        return headers, query_params

    def _resolve_mapping_secret(self, mapping: CredentialMapping) -> str | None:
        if mapping.secret_name not in self._registry.allowed_secrets:
            return None
        return self._resolver.resolve(mapping.secret_name)

    def _inject_mapping(
        self,
        mapping: CredentialMapping,
        headers: dict[str, str],
        query_params: dict[str, str] | None,
    ) -> dict[str, str] | None:
        secret = self._resolve_mapping_secret(mapping)
        if secret is None:
            return query_params

        if mapping.method == InjectionMethod.BEARER:
            headers["Authorization"] = f"Bearer {secret}"
        elif mapping.method == InjectionMethod.BASIC:
            user = mapping.username or ""
            encoded = base64.b64encode(f"{user}:{secret}".encode()).decode()
            headers["Authorization"] = f"Basic {encoded}"
        elif mapping.method == InjectionMethod.HEADER:
            header_name = mapping.header_name or "X-API-Key"
            headers[header_name] = secret
        elif mapping.method == InjectionMethod.QUERY:
            param_name = mapping.query_param or "key"
            query_params = query_params or {}
            query_params[param_name] = secret

        return query_params

    def resolve_secret_refs(
        self,
        headers: dict[str, str],
        query_params: dict[str, str] | None = None,
    ) -> tuple[dict[str, str], dict[str, str] | None]:
        """Resolve ``secret://name`` references in headers and query params."""
        return (
            resolve_inline_mapping_refs(dict(headers), self._resolver.resolve) or {},
            resolve_inline_mapping_refs(
                dict(query_params) if query_params is not None else None,
                self._resolver.resolve,
            ),
        )

    def prepare_request(
        self,
        url: str,
        headers: dict[str, str],
        query_params: dict[str, str] | None = None,
    ) -> tuple[dict[str, str], dict[str, str] | None]:
        """Apply host-based injection and secret reference resolution for a request."""
        host = urlparse(url).hostname or ""
        injected_headers, injected_query = self.inject_headers(host, headers, query_params)
        return self.resolve_secret_refs(injected_headers, injected_query)

    def prepare_url_and_headers(
        self,
        url: str,
        headers: dict[str, str] | None = None,
    ) -> tuple[str, dict[str, str]]:
        """Resolve host credentials and ``secret://`` refs embedded in *url* or *headers*."""
        parsed = urlparse(url)
        query_params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        prepared_headers, prepared_query = self.prepare_request(url, headers or {}, query_params)
        final_query = urlencode(prepared_query or {}, doseq=True)
        prepared_url = urlunparse(parsed._replace(query=final_query))
        return prepared_url, prepared_headers

    def resolve_value(self, value: str) -> str:
        """Resolve embedded ``secret://name`` references in a string value."""
        return resolve_inline_secret_refs(value, self._resolver.resolve)


class SecretResolver:
    """Interface for resolving secret values by name."""

    def resolve(self, name: str) -> str | None:
        """Return the plaintext secret value, or None if not found."""
        raise NotImplementedError


class EnvSecretResolver(SecretResolver):
    """Resolve secrets from environment variables (transitional)."""

    def __init__(self, env_map: dict[str, str] | None = None) -> None:
        """*env_map* maps secret_name → env var name."""
        self._env_map = env_map or {}

    def resolve(self, name: str) -> str | None:
        import os

        env_var = self._env_map.get(name, name.upper())
        return os.environ.get(env_var)


class EncryptedSecretResolver(SecretResolver):
    """Resolve secrets from the encrypted auth store."""

    def __init__(self) -> None:
        self._cache: dict[str, str] = {}

    def resolve(self, name: str) -> str | None:
        if name in self._cache:
            return self._cache[name]

        from src.auth.store import get_api_key_for_provider

        # Map secret names to provider lookups
        key = get_api_key_for_provider(name)
        if key:
            self._cache[name] = key
        return key


def _host_matches(host: str, patterns: list[str]) -> bool:
    """Check if *host* matches any of the *patterns* (supports wildcards)."""
    host = host.lower()
    for pattern in patterns:
        pattern = pattern.lower()
        if fnmatch.fnmatch(host, pattern):
            return True
        # Support *.example.com matching sub.example.com
        if pattern.startswith("*.") and host.endswith(pattern[1:]):
            return True
    return False


def build_default_registry() -> CredentialRegistry:
    """Default host mappings for built-in HTTP tools."""
    registry = CredentialRegistry()
    registry.add_mapping(
        CredentialMapping(
            secret_name="brave",
            method=InjectionMethod.HEADER,
            host_patterns=["api.search.brave.com"],
            header_name="X-Subscription-Token",
        )
    )
    return registry
