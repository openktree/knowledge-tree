"""SSRF guard tests for `validate_fetch_url`.

These run entirely against monkeypatched DNS so they don't need the
network and can't be flaked by it.
"""

from __future__ import annotations

import socket
from typing import Any

import pytest

from kt_providers.fetch.url_safety import (
    ALLOWED_SCHEMES,
    UnsafeUrlError,
    validate_fetch_url,
)


def _addrinfo(ip: str, family: int = socket.AF_INET) -> list[tuple[Any, ...]]:
    """Build a fake getaddrinfo result list with one entry for `ip`."""
    return [(family, socket.SOCK_STREAM, 0, "", (ip, 0))]


def _patch_dns(monkeypatch: pytest.MonkeyPatch, mapping: dict[str, list[tuple[Any, ...]]]) -> None:
    """Replace the loop's getaddrinfo with a static lookup table.

    Looks up by hostname; raises `socket.gaierror` for unknown names so
    the validator's "DNS resolution failed" branch is also testable.
    """

    async def fake_getaddrinfo(host: str, *_args: Any, **_kwargs: Any) -> list[tuple[Any, ...]]:
        if host in mapping:
            return mapping[host]
        raise socket.gaierror(f"unknown host {host}")

    import asyncio

    real_loop = asyncio.get_event_loop_policy()._loop_factory  # type: ignore[attr-defined]
    # We patch on the running loop instead — getaddrinfo is an instance method.
    loop = asyncio.get_event_loop()
    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
    _ = real_loop  # silence unused


# ── scheme rejection ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rejects_file_scheme():
    with pytest.raises(UnsafeUrlError, match="scheme"):
        await validate_fetch_url("file:///etc/passwd")


@pytest.mark.asyncio
async def test_rejects_gopher_scheme():
    with pytest.raises(UnsafeUrlError, match="scheme"):
        await validate_fetch_url("gopher://example.com/")


@pytest.mark.asyncio
async def test_rejects_javascript_scheme():
    with pytest.raises(UnsafeUrlError, match="scheme"):
        await validate_fetch_url("javascript:alert(1)")


@pytest.mark.asyncio
async def test_rejects_data_scheme():
    with pytest.raises(UnsafeUrlError, match="scheme"):
        await validate_fetch_url("data:text/html,<h1>x</h1>")


@pytest.mark.asyncio
async def test_rejects_no_scheme():
    with pytest.raises(UnsafeUrlError):
        await validate_fetch_url("//example.com/x")


@pytest.mark.asyncio
async def test_allowed_schemes_set_is_http_https():
    # Pin the policy: only http and https are ever fetchable.
    assert ALLOWED_SCHEMES == frozenset({"http", "https"})


# ── IP-literal rejection ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rejects_loopback_ipv4_literal(monkeypatch):
    _patch_dns(monkeypatch, {"127.0.0.1": _addrinfo("127.0.0.1")})
    with pytest.raises(UnsafeUrlError, match="loopback|private|reserved"):
        await validate_fetch_url("http://127.0.0.1/admin")


@pytest.mark.asyncio
async def test_rejects_loopback_ipv6_literal(monkeypatch):
    _patch_dns(monkeypatch, {"::1": _addrinfo("::1", family=socket.AF_INET6)})
    with pytest.raises(UnsafeUrlError):
        await validate_fetch_url("http://[::1]/")


@pytest.mark.asyncio
async def test_rejects_private_10_range(monkeypatch):
    _patch_dns(monkeypatch, {"10.0.0.5": _addrinfo("10.0.0.5")})
    with pytest.raises(UnsafeUrlError):
        await validate_fetch_url("http://10.0.0.5/")


@pytest.mark.asyncio
async def test_rejects_private_192_range(monkeypatch):
    _patch_dns(monkeypatch, {"192.168.1.1": _addrinfo("192.168.1.1")})
    with pytest.raises(UnsafeUrlError):
        await validate_fetch_url("http://192.168.1.1/")


@pytest.mark.asyncio
async def test_rejects_aws_metadata_endpoint(monkeypatch):
    _patch_dns(monkeypatch, {"169.254.169.254": _addrinfo("169.254.169.254")})
    with pytest.raises(UnsafeUrlError, match="reserved|private|loopback"):
        await validate_fetch_url("http://169.254.169.254/latest/meta-data/iam/security-credentials/")


@pytest.mark.asyncio
async def test_rejects_unspecified_address(monkeypatch):
    _patch_dns(monkeypatch, {"0.0.0.0": _addrinfo("0.0.0.0")})
    with pytest.raises(UnsafeUrlError):
        await validate_fetch_url("http://0.0.0.0/")


# ── DNS-resolved rejection (defends against `localhost`, internal DNS) ──


@pytest.mark.asyncio
async def test_rejects_localhost_via_dns(monkeypatch):
    _patch_dns(monkeypatch, {"localhost": _addrinfo("127.0.0.1")})
    with pytest.raises(UnsafeUrlError, match="loopback"):
        await validate_fetch_url("http://localhost/")


@pytest.mark.asyncio
async def test_rejects_internal_dns_name_resolving_to_private(monkeypatch):
    _patch_dns(monkeypatch, {"redis.internal": _addrinfo("10.0.5.6")})
    with pytest.raises(UnsafeUrlError):
        await validate_fetch_url("http://redis.internal:6379/")


@pytest.mark.asyncio
async def test_rejects_when_any_address_in_round_robin_is_private(monkeypatch):
    """A hostname with both public and private A records is rejected — the
    public one is just enough cover for a DNS-rebinding-style trick."""
    _patch_dns(
        monkeypatch,
        {
            "tricky.example": [
                (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.1", 0)),
            ]
        },
    )
    with pytest.raises(UnsafeUrlError):
        await validate_fetch_url("http://tricky.example/")


@pytest.mark.asyncio
async def test_rejects_when_dns_fails(monkeypatch):
    _patch_dns(monkeypatch, {})  # nothing resolves
    with pytest.raises(UnsafeUrlError, match="DNS"):
        await validate_fetch_url("http://nonexistent.example/")


# ── public hosts pass ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_accepts_public_https_host(monkeypatch):
    _patch_dns(monkeypatch, {"example.com": _addrinfo("23.215.0.136")})
    # Should not raise.
    await validate_fetch_url("https://example.com/article")


@pytest.mark.asyncio
async def test_accepts_public_ipv6_host(monkeypatch):
    _patch_dns(
        monkeypatch, {"example.com": _addrinfo("2606:2800:21f:cb07:6820:80da:af6b:8b2c", family=socket.AF_INET6)}
    )
    await validate_fetch_url("https://example.com/")
