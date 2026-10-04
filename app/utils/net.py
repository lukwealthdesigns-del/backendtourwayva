"""Client IP extraction that cannot be spoofed by the caller.

The leftmost X-Forwarded-For entry is fully attacker-controlled: any
client can send `X-Forwarded-For: 1.2.3.4` and, if the app trusted it,
rotate its apparent IP on every request to dodge rate limits and IP
blocks. Only entries appended by OUR OWN proxies can be trusted, and
those are the RIGHTMOST ones.

`trusted_proxy_hops` is the number of reverse proxies / load balancers
we operate in front of the app (0 = none: use the socket peer address
and ignore X-Forwarded-For entirely).
"""
from __future__ import annotations

import ipaddress
from typing import Optional


def _valid_ip(candidate: str) -> Optional[str]:
    try:
        return str(ipaddress.ip_address(candidate.strip()))
    except ValueError:
        return None


def extract_client_ip(
    *,
    forwarded_for: Optional[str],
    peer_host: Optional[str],
    trusted_proxy_hops: int,
) -> Optional[str]:
    if trusted_proxy_hops <= 0 or not forwarded_for:
        return peer_host

    hops = [h.strip() for h in forwarded_for.split(",") if h.strip()]
    if len(hops) < trusted_proxy_hops:
        # Fewer entries than proxies we run => the header did not pass
        # through our infrastructure as expected; do not trust it.
        return peer_host

    candidate = _valid_ip(hops[-trusted_proxy_hops])
    return candidate or peer_host


def is_public_ip(candidate: Optional[str]) -> bool:
    """True only for a routable public address. Loopback (127.0.0.1 in local development),
    private LAN ranges, link-local and reserved addresses have no meaningful geolocation, so
    they are never sent to an IP-geolocation provider."""
    if not candidate:
        return False
    try:
        ip = ipaddress.ip_address(candidate.strip())
    except ValueError:
        return False
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified
    )
