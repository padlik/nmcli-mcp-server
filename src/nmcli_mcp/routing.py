"""Route-probe derivation and ``ip route get`` output parsing.

Pure functions only: no subprocess execution lives here (the nmcli/ip
adapter owns ``ip route get`` execution); ``vpn.py`` drives it.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass

IPv4Network = ipaddress.IPv4Network
IPv6Network = ipaddress.IPv6Network


@dataclass(frozen=True)
class RouteProbe:
    """Deterministic probe derived from an expected route network.

    Attributes:
        network: The expected route as a parsed IPv4 or IPv6 network.
        probe_address: The address used for ``ip route get`` probing.
    """

    network: IPv4Network | IPv6Network
    probe_address: str


@dataclass(frozen=True)
class RouteCheck:
    """Result of checking a single expected route.

    Attributes:
        cidr: Original CIDR string for reporting.
        ok: Whether the route resolves through the expected interface.
        probe: Probe address used for the lookup.
        resolved_interface: Interface returned by ``ip route get``, if parseable.
        detail: Human-readable explanation of the result.
    """

    cidr: str
    ok: bool
    probe: str
    resolved_interface: str | None
    detail: str


# Keywords that can appear in ``ip route get`` output after a ``dev`` token is
# missing or malformed. They are used only to skip a stray ``dev`` token that is
# not actually followed by an interface name.
_ROUTE_OUTPUT_KEYWORDS = frozenset(
    {
        "cache",
        "expires",
        "from",
        "iif",
        "lookup",
        "metric",
        "mtu",
        "onlink",
        "pref",
        "proto",
        "scope",
        "src",
        "table",
        "uid",
        "via",
    }
)


def derive_probe(network: IPv4Network | IPv6Network) -> RouteProbe:
    """Derive a deterministic probe address from ``network``.

    For prefixes that have usable host space (up to and including /30 for IPv4,
    or /126 for IPv6) the probe is the first host address
    (``network_address + 1``). For single-host or point-to-point prefixes
    (/31, /32 IPv4; /127, /128 IPv6) the probe is the network address itself,
    ensuring it stays inside the route's network.

    Args:
        network: Parsed expected route network.

    Returns:
        A ``RouteProbe`` containing the network and probe address string.
    """
    threshold = network.max_prefixlen - 2
    if network.prefixlen > threshold:
        probe_address = network.network_address
    else:
        probe_address = network.network_address + 1
    return RouteProbe(network=network, probe_address=str(probe_address))


def parse_route_get(output: str) -> str | None:
    """Parse ``ip route get`` stdout and return the interface after ``dev``.

    Only the literal ``dev <iface>`` token pair is recognised. The interface
    name must look like a real Linux interface identifier (letters, digits,
    underscore, hyphen, dot) so that random words following a stray ``dev``
    token are rejected. If ``dev`` is absent, not followed by an interface
    name, or followed by another routing keyword, this returns ``None`` so the
    caller can report a structured route check failure rather than raising.

    Args:
        output: Raw stdout from ``ip route get <probe>``.

    Returns:
        Resolved interface name, or ``None`` when no ``dev`` token is found.
    """
    tokens = output.split()
    for index, token in enumerate(tokens):
        if token != "dev":
            continue
        if index + 1 >= len(tokens):
            return None
        candidate = tokens[index + 1]
        if candidate in _ROUTE_OUTPUT_KEYWORDS:
            return None
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", candidate):
            return None
        return candidate
    return None


def evaluate_route(
    network: IPv4Network | IPv6Network,
    active_interface: str,
    route_get_stdout: str,
) -> RouteCheck:
    """Evaluate whether ``network`` routes through ``active_interface``.

    Derives the probe address, parses the ``ip route get`` output, and compares
    the resolved interface. All error conditions are returned as structured
    ``RouteCheck`` results with ``ok=False``; this function never raises for
    malformed input.

    Args:
        network: Parsed expected route network.
        active_interface: Name of the VPN tunnel/interface to match.
        route_get_stdout: Raw stdout from ``ip route get <probe>``. Empty or
            whitespace-only input is treated as a failed lookup.

    Returns:
        A structured ``RouteCheck`` result.
    """
    probe = derive_probe(network)
    cidr = str(network)

    if not route_get_stdout or not route_get_stdout.strip():
        return RouteCheck(
            cidr=cidr,
            ok=False,
            probe=probe.probe_address,
            resolved_interface=None,
            detail="route lookup produced no output",
        )

    resolved_interface = parse_route_get(route_get_stdout)
    if resolved_interface is None:
        return RouteCheck(
            cidr=cidr,
            ok=False,
            probe=probe.probe_address,
            resolved_interface=None,
            detail=f"probe {probe.probe_address} route get output unparseable",
        )

    if resolved_interface == active_interface:
        return RouteCheck(
            cidr=cidr,
            ok=True,
            probe=probe.probe_address,
            resolved_interface=resolved_interface,
            detail=(
                f"probe {probe.probe_address} resolved via {resolved_interface} "
                f"(expected {active_interface})"
            ),
        )

    return RouteCheck(
        cidr=cidr,
        ok=False,
        probe=probe.probe_address,
        resolved_interface=resolved_interface,
        detail=(
            f"probe {probe.probe_address} resolved via {resolved_interface} "
            f"(expected {active_interface})"
        ),
    )
