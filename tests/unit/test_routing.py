from __future__ import annotations

import ipaddress

import pytest

from nmcli_mcp.routing import (
    RouteCheck,
    RouteProbe,
    derive_probe,
    evaluate_route,
    parse_route_get,
)


class TestDeriveProbe:
    """Deterministic probe selection per design decision 4."""

    def test_slash_16_yields_first_host(self) -> None:
        network = ipaddress.ip_network("10.13.0.0/16")
        probe = derive_probe(network)
        assert probe == RouteProbe(network=network, probe_address="10.13.0.1")

    def test_slash_30_yields_first_host(self) -> None:
        network = ipaddress.ip_network("10.8.0.0/30")
        probe = derive_probe(network)
        assert probe == RouteProbe(network=network, probe_address="10.8.0.1")

    def test_slash_31_yields_network_address(self) -> None:
        network = ipaddress.ip_network("10.8.0.4/31")
        probe = derive_probe(network)
        assert probe == RouteProbe(network=network, probe_address="10.8.0.4")

    def test_slash_32_yields_network_address(self) -> None:
        network = ipaddress.ip_network("10.8.0.5/32")
        probe = derive_probe(network)
        assert probe == RouteProbe(network=network, probe_address="10.8.0.5")

    def test_ipv6_slash_64_yields_first_host(self) -> None:
        network = ipaddress.ip_network("fd00:abcd::/64")
        probe = derive_probe(network)
        assert probe == RouteProbe(network=network, probe_address="fd00:abcd::1")

    def test_ipv6_slash_128_yields_network_address(self) -> None:
        network = ipaddress.ip_network("fd00:abcd::5/128")
        probe = derive_probe(network)
        assert probe == RouteProbe(network=network, probe_address="fd00:abcd::5")


class TestParseRouteGet:
    """Parsing the ``dev <iface>`` token from ``ip route get`` output."""

    def test_parse_ipv4_with_table_uid_and_cache(self) -> None:
        output = "10.13.0.1 dev wg0 table 51820 src 10.13.0.5 uid 1000\n    cache"
        assert parse_route_get(output) == "wg0"

    def test_parse_ipv4_short_output(self) -> None:
        output = "10.8.0.5 dev wg0 src 10.13.0.5"
        assert parse_route_get(output) == "wg0"

    def test_parse_ipv6_via_dev(self) -> None:
        output = "fd00:abcd::1 from :: dev wg0 metric 1024 pref medium"
        assert parse_route_get(output) == "wg0"

    def test_parse_unparseable_network_unreachable(self) -> None:
        output = "RTNETLINK answers: Network is unreachable"
        assert parse_route_get(output) is None

    def test_parse_ipv6_local_table_no_dev_returns_none(self) -> None:
        output = "fd00:abcd::1 from :: iif lo lookup main uid 1000 cache"
        assert parse_route_get(output) is None

    def test_parse_dev_followed_by_keyword_returns_none(self) -> None:
        output = "10.13.0.1 dev via something"
        assert parse_route_get(output) is None

    def test_parse_dev_at_end_of_string_returns_none(self) -> None:
        output = "10.13.0.1 dev"
        assert parse_route_get(output) is None

    def test_parse_only_dev_token_returns_none(self) -> None:
        output = "dev"
        assert parse_route_get(output) is None


class TestEvaluateRoute:
    """End-to-end route evaluation without subprocesses."""

    def test_slash_16_through_tunnel(self) -> None:
        network = ipaddress.ip_network("10.13.0.0/16")
        output = "10.13.0.1 dev wg0 table 51820 src 10.13.0.5 uid 1000\n    cache"
        result = evaluate_route(network, "wg0", output)
        assert result == RouteCheck(
            cidr="10.13.0.0/16",
            ok=True,
            probe="10.13.0.1",
            resolved_interface="wg0",
            detail="probe 10.13.0.1 resolved via wg0 (expected wg0)",
        )

    def test_slash_16_via_default_route(self) -> None:
        network = ipaddress.ip_network("10.12.0.0/16")
        output = "10.12.0.1 dev enp0s2 src 192.168.5.23"
        result = evaluate_route(network, "wg0", output)
        assert result == RouteCheck(
            cidr="10.12.0.0/16",
            ok=False,
            probe="10.12.0.1",
            resolved_interface="enp0s2",
            detail="probe 10.12.0.1 resolved via enp0s2 (expected wg0)",
        )

    def test_slash_32_probe_stays_inside_network(self) -> None:
        network = ipaddress.ip_network("10.8.0.5/32")
        output = "10.8.0.5 dev wg0 src 10.13.0.5"
        result = evaluate_route(network, "wg0", output)
        assert result.probe == "10.8.0.5"
        assert result.ok is True
        assert result.resolved_interface == "wg0"

    def test_ipv6_slash_64_resolves_through_wg0(self) -> None:
        network = ipaddress.ip_network("fd00:abcd::/64")
        output = "fd00:abcd::1 from :: dev wg0 metric 1024 pref medium"
        result = evaluate_route(network, "wg0", output)
        assert result.probe == "fd00:abcd::1"
        assert result.ok is True
        assert result.resolved_interface == "wg0"

    def test_unparseable_output_yields_structured_failure(self) -> None:
        network = ipaddress.ip_network("10.13.0.0/16")
        output = "RTNETLINK answers: Network is unreachable"
        result = evaluate_route(network, "wg0", output)
        assert result.ok is False
        assert result.resolved_interface is None
        assert "unparseable" in result.detail

    def test_empty_stdout_yields_lookup_failure(self) -> None:
        network = ipaddress.ip_network("10.13.0.0/16")
        result = evaluate_route(network, "wg0", "")
        assert result.ok is False
        assert result.resolved_interface is None
        assert result.probe == "10.13.0.1"
        assert "no output" in result.detail

    def test_whitespace_only_stdout_yields_lookup_failure(self) -> None:
        network = ipaddress.ip_network("10.13.0.0/16")
        result = evaluate_route(network, "wg0", "   \n  ")
        assert result.ok is False
        assert result.resolved_interface is None
        assert "no output" in result.detail

    @pytest.mark.parametrize(
        "output",
        [
            "\x00\x01\x02 dev _garbage",
            "only dev _nothing after",
            "dev",
            "via enp0s2 dev",
            "probe 10.13.0.1 dev via wg0",
        ],
    )
    def test_never_raises_on_adversarial_output(self, output: str) -> None:
        network = ipaddress.ip_network("10.13.0.0/16")
        result = evaluate_route(network, "wg0", output)
        assert result.ok is False
        assert result.resolved_interface is None
