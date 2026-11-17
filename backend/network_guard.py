"""
Network rules for attendance check-ins.

1. Session network lock (the main rule). When a faculty member starts a
   session, the network they are on is recorded with it, and students must
   check in from that same network. Nothing has to be configured, so the same
   code works on a home Wi-Fi today and the college network later.

     * private/LAN addresses are locked to their /24 (NETWORK_LOCK_PREFIX_V4)
     * public addresses (a cloud server sees the college's shared address) are
       locked to that exact address (NETWORK_LOCK_PUBLIC_PREFIX_V4=32)
     * IPv6 uses /64
     * a faculty browser on the server machine itself (localhost) is treated
       as being on the machine's own LAN
     * the server machine itself (loopback) may always check in (local testing)

   The faculty can also allow the network a blocked student came from (the
   dashboard lists blocked attempts), or switch the lock off for testing.

2. ALLOWED_NETWORKS (optional outer limit). Comma-separated CIDRs, or "*"
   (the default) for no extra limit. Applied on top of the session lock.

Behind a hosting proxy (Render, the https dev server, ...) the socket address
is the proxy's, not the student's. Set TRUST_PROXY_HOPS to the number of
proxies in front of the app so Flask reads the real client address from
X-Forwarded-For (see app.py). Leave it at 0 when the app is reachable directly,
otherwise the header could be spoofed.
"""

import ipaddress
import os
import socket

DEFAULT_ALLOWED_NETWORKS = "*"


def _normalize(value):
    """An ip_address object (IPv4-mapped IPv6 unwrapped), or None."""
    try:
        address = ipaddress.ip_address(value)
    except (TypeError, ValueError):
        return None

    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped

    return address


def _int_env(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def server_lan_ip():
    """This machine's address on the local network (what phones can reach)."""
    lan_ip = "127.0.0.1"
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("10.255.255.255", 1))
        lan_ip = probe.getsockname()[0]
        probe.close()
    except Exception:
        pass
    return lan_ip


# ---------------------------------------------------------------------------
# Outer limit: ALLOWED_NETWORKS
# ---------------------------------------------------------------------------

def _allowed_networks():
    raw = os.getenv("ALLOWED_NETWORKS", DEFAULT_ALLOWED_NETWORKS).strip()

    if raw.lower() in ("*", "any", "all"):
        return None

    networks = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            networks.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            print(f"[network_guard] Ignoring invalid ALLOWED_NETWORKS entry: {part!r}")

    return networks


def is_allowed_client(client_ip):
    networks = _allowed_networks()

    if networks is None:
        return True

    address = _normalize(client_ip)

    if address is None:
        return False

    return any(
        address in network
        for network in networks
        if network.version == address.version
    )


# ---------------------------------------------------------------------------
# Session network lock
# ---------------------------------------------------------------------------

def network_of(client_ip):
    """The network string a client address is locked/reported as, or None."""
    address = _normalize(client_ip)

    if address is None:
        return None

    # A browser on the server machine reaches it over loopback, but phones
    # reach it over the machine's LAN address: lock to that LAN instead.
    if address.is_loopback:
        address = _normalize(server_lan_ip()) or address

    if address.version == 6:
        prefix = 64
    elif address.is_global:
        prefix = _int_env("NETWORK_LOCK_PUBLIC_PREFIX_V4", 32)
    else:
        prefix = _int_env("NETWORK_LOCK_PREFIX_V4", 24)

    prefix = max(8, min(prefix, address.max_prefixlen))

    return str(ipaddress.ip_network(f"{address}/{prefix}", strict=False))


def make_session_lock(faculty_ip, allow_any=False):
    """The lock stored with a new session."""
    if allow_any:
        return {"mode": "any", "networks": []}

    network = network_of(faculty_ip)

    return {"mode": "networks", "networks": [network] if network else []}


def client_in_lock(lock, client_ip):
    """True if this client may check in to a session with this lock."""
    if not lock:  # session created before locks existed
        return True

    if lock.get("mode") == "any":
        return True

    address = _normalize(client_ip)

    if address is None:
        return False

    if address.is_loopback:  # the server machine itself
        return True

    for network in lock.get("networks", []):
        try:
            parsed = ipaddress.ip_network(network, strict=False)
        except ValueError:
            continue
        if parsed.version == address.version and address in parsed:
            return True

    return False


def valid_network(value):
    """A normalised CIDR string, or None if `value` isn't one."""
    try:
        return str(ipaddress.ip_network(str(value).strip(), strict=False))
    except ValueError:
        return None


def lock_is_private_lan(lock):
    """
    True when every locked network is a private LAN range. There each phone has
    its own address, so "one address = one phone" is a safe rule (it is not on
    a shared public address, where a whole college looks like one address).
    """
    networks = (lock or {}).get("networks") or []

    if not networks:
        return False

    try:
        return all(ipaddress.ip_network(n, strict=False).is_private for n in networks)
    except ValueError:
        return False


def is_loopback(client_ip):
    address = _normalize(client_ip)
    return bool(address and address.is_loopback)

