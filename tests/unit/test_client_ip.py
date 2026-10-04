"""X-Forwarded-For must only be trusted for OUR OWN proxies."""
from app.utils.net import extract_client_ip


def test_ignores_forwarded_for_when_no_trusted_proxy():
    # A client can send any X-Forwarded-For; with 0 trusted hops it is ignored.
    assert extract_client_ip(forwarded_for="6.6.6.6", peer_host="10.0.0.9", trusted_proxy_hops=0) == "10.0.0.9"


def test_spoofed_leftmost_entry_is_never_used():
    # attacker sent "6.6.6.6"; our single proxy appended the real peer.
    ip = extract_client_ip(forwarded_for="6.6.6.6, 203.0.113.7", peer_host="10.0.0.1", trusted_proxy_hops=1)
    assert ip == "203.0.113.7"


def test_two_trusted_proxies_pick_second_from_right():
    ip = extract_client_ip(forwarded_for="6.6.6.6, 203.0.113.7, 10.0.0.2", peer_host="10.0.0.1", trusted_proxy_hops=2)
    assert ip == "203.0.113.7"


def test_fewer_entries_than_proxies_falls_back_to_peer():
    assert extract_client_ip(forwarded_for="203.0.113.7", peer_host="10.0.0.1", trusted_proxy_hops=2) == "10.0.0.1"


def test_invalid_ip_in_header_falls_back_to_peer():
    assert extract_client_ip(forwarded_for="not-an-ip", peer_host="10.0.0.1", trusted_proxy_hops=1) == "10.0.0.1"


def test_missing_header_uses_peer():
    assert extract_client_ip(forwarded_for=None, peer_host="10.0.0.1", trusted_proxy_hops=1) == "10.0.0.1"
