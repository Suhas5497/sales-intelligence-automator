"""URL safety checks: only public http(s) hosts may be fetched (no SSRF into private networks)."""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeURL(ValueError):
    pass


def check_url(url: str, resolve: bool = True) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeURL(f"scheme '{parsed.scheme}' is not allowed")
    host = parsed.hostname
    if not host:
        raise UnsafeURL("URL has no host")
    if host == "localhost" or host.endswith((".local", ".internal", ".localhost")):
        raise UnsafeURL(f"host '{host}' is local")
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        if not resolve:
            return
        try:
            addresses = [ipaddress.ip_address(info[4][0]) for info in socket.getaddrinfo(host, None)]
        except socket.gaierror:
            return  # DNS failure is reported by the fetcher as DNS_NOT_RESOLVED
    for ip in addresses:
        if not ip.is_global:
            raise UnsafeURL(f"host '{host}' resolves to non-public address {ip}")
