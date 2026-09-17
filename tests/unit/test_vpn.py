"""Unit tests for ``nmcli_mcp.vpn.VpnService``.

The real ``nmcli``/``ip`` binaries are never invoked. A programmable
``FakeRunner`` records every call so the orchestration logic can be asserted
independently of subprocess behavior.
"""

from __future__ import annotations

import ipaddress
import json
from typing import Any

import pytest

from nmcli_mcp.config import VpnProfile
from nmcli_mcp.nmcli import NmcliResult, parse_active_connections
from nmcli_mcp.vpn import VpnService

from ._doubles import FakeRunner, _fail, _ok, _timeout


@pytest.fixture
def config() -> dict[str, VpnProfile]:
    return {
        "intetics": VpnProfile(
            id="intetics",
            connection="Intetics VPN",
            expected_routes=(
                ipaddress.ip_network("10.13.0.0/16"),
                ipaddress.ip_network("10.12.0.0/16"),
            ),
        ),
        "testvpn": VpnProfile(
            id="testvpn",
            connection="Test VPN",
            expected_routes=(ipaddress.ip_network("192.168.200.0/24"),),
        ),
    }


@pytest.fixture
def service(config: dict[str, VpnProfile]) -> VpnService:
    return VpnService(runner=FakeRunner({}), config=config)


class TestListVpns:
    """``list_vpns`` returns config without touching the runner."""

    async def test_list_sorted_and_no_runner_calls(
        self, service: VpnService
    ) -> None:
        result = await service.list_vpns()

        assert result == {
            "ok": True,
            "vpns": [
                {"id": "intetics", "connection": "Intetics VPN"},
                {"id": "testvpn", "connection": "Test VPN"},
            ],
        }
        assert service.runner.calls == []


class TestStatus:
    """``status`` reports active/inactive state and bound device."""

    async def test_active_with_device(self, config: dict[str, VpnProfile]) -> None:
        runner = FakeRunner(
            {
                "connection_show_active": _ok(
                    stdout="Intetics VPN:vpn:wg0\nOther:wifi:wlp1s0\n"
                )
            }
        )
        service = VpnService(runner=runner, config=config)

        result = await service.status("intetics")

        assert result == {
            "ok": True,
            "id": "intetics",
            "connection": "Intetics VPN",
            "connected": True,
            "device": "wg0",
        }
        assert runner.calls == [("connection_show_active", ())]

    async def test_active_with_empty_device(self, config: dict[str, VpnProfile]) -> None:
        runner = FakeRunner(
            {"connection_show_active": _ok(stdout="Intetics VPN:vpn:\n")}
        )
        service = VpnService(runner=runner, config=config)

        result = await service.status("intetics")

        assert result == {
            "ok": True,
            "id": "intetics",
            "connection": "Intetics VPN",
            "connected": True,
            "device": None,
        }

    async def test_inactive(self, config: dict[str, VpnProfile]) -> None:
        runner = FakeRunner(
            {"connection_show_active": _ok(stdout="Other:wifi:wlp1s0\n")}
        )
        service = VpnService(runner=runner, config=config)

        result = await service.status("intetics")

        assert result == {
            "ok": True,
            "id": "intetics",
            "connection": "Intetics VPN",
            "connected": False,
            "device": None,
        }

    async def test_connection_name_with_colon(self, config: dict[str, VpnProfile]) -> None:
        escaped = r"Intetics\::vpn:wg0"
        rows = parse_active_connections(escaped)
        assert rows[0].name == "Intetics:"

        runner = FakeRunner({"connection_show_active": _ok(stdout=escaped + "\n")})
        config_with_colon = dict(config)
        config_with_colon["colon-vpn"] = VpnProfile(
            id="colon-vpn",
            connection="Intetics:",
            expected_routes=(),
        )
        service = VpnService(runner=runner, config=config_with_colon)

        result = await service.status("colon-vpn")

        assert result == {
            "ok": True,
            "id": "colon-vpn",
            "connection": "Intetics:",
            "connected": True,
            "device": "wg0",
        }

    async def test_nmcli_failure(self, config: dict[str, VpnProfile]) -> None:
        runner = FakeRunner(
            {
                "connection_show_active": _fail(
                    exit_code=1, stderr="Error: nmcli not running"
                )
            }
        )
        service = VpnService(runner=runner, config=config)

        result = await service.status("intetics")

        assert result["ok"] is False
        assert result["error"] == "nmcli_failed"
        assert result["exit_code"] == 1
        assert result["stderr"] == "Error: nmcli not running"


class TestConnect:
    """``connect`` is idempotent and verifies the final state."""

    async def test_already_active_runs_no_activation(
        self, config: dict[str, VpnProfile]
    ) -> None:
        runner = FakeRunner(
            {"connection_show_active": _ok(stdout="Intetics VPN:vpn:wg0\n")}
        )
        service = VpnService(runner=runner, config=config)

        result = await service.connect("intetics")

        assert result == {
            "ok": True,
            "id": "intetics",
            "connection": "Intetics VPN",
            "connected": True,
            "device": "wg0",
            "already_connected": True,
        }
        assert "connection_up" not in [call[0] for call in runner.calls]

    async def test_inactive_activates_and_verifies(
        self, config: dict[str, VpnProfile]
    ) -> None:
        calls = ["Other:wifi:wlp1s0\n", "Intetics VPN:vpn:wg0\n"]

        async def active_response(_method: str, _args: tuple[Any, ...]) -> NmcliResult:
            return _ok(stdout=calls.pop(0))

        responses: dict[str, Any] = {
            "connection_show_active": active_response,
            "connection_up": _ok(),
        }
        runner = FakeRunner(responses)
        service = VpnService(runner=runner, config=config)

        result = await service.connect("intetics")

        assert result == {
            "ok": True,
            "id": "intetics",
            "connection": "Intetics VPN",
            "connected": True,
            "device": "wg0",
            "already_connected": False,
        }
        assert runner.calls == [
            ("connection_show_active", ()),
            ("connection_up", ("Intetics VPN",)),
            ("connection_show_active", ()),
        ]

    async def test_connection_up_fails(self, config: dict[str, VpnProfile]) -> None:
        runner = FakeRunner(
            {
                "connection_show_active": _ok(stdout="Other:wifi:wlp1s0\n"),
                "connection_up": _fail(
                    exit_code=1, stderr="Error: Connection activation failed"
                ),
            }
        )
        service = VpnService(runner=runner, config=config)

        result = await service.connect("intetics")

        assert result["ok"] is False
        assert result["error"] == "connect_failed"
        assert result["stderr"] == "Error: Connection activation failed"
        assert result["timed_out"] is False

    async def test_up_succeeds_but_reverify_inactive(
        self, config: dict[str, VpnProfile]
    ) -> None:
        calls = ["Other:wifi:wlp1s0\n", "Other:wifi:wlp1s0\n"]

        async def active_response(_method: str, _args: tuple[Any, ...]) -> NmcliResult:
            return _ok(stdout=calls.pop(0))

        runner = FakeRunner(
            {
                "connection_show_active": active_response,
                "connection_up": _ok(),
            }
        )
        service = VpnService(runner=runner, config=config)

        result = await service.connect("intetics")

        assert result["ok"] is False
        assert result["error"] == "connect_failed"
        assert result["connected"] is False


class TestDisconnect:
    """``disconnect`` deactivates and verifies post-state."""

    async def test_disconnect_success(self, config: dict[str, VpnProfile]) -> None:
        active_calls = [
            # pre-check
            "Intetics VPN:vpn:wg0\n",
            # post-down verify
            "Other:wifi:wlp1s0\n",
        ]

        async def active_response(_method: str, _args: tuple[Any, ...]) -> NmcliResult:
            return _ok(stdout=active_calls.pop(0))

        runner = FakeRunner(
            {
                "connection_down": _ok(),
                "connection_show_active": active_response,
            }
        )
        service = VpnService(runner=runner, config=config)

        result = await service.disconnect("intetics")

        assert result == {
            "ok": True,
            "id": "intetics",
            "connection": "Intetics VPN",
            "connected": False,
            "device": None,
            "already_disconnected": False,
        }
        assert runner.calls == [
            ("connection_show_active", ()),
            ("connection_down", ("Intetics VPN",)),
            ("connection_show_active", ()),
        ]

    async def test_disconnect_already_inactive(self, config: dict[str, VpnProfile]) -> None:
        runner = FakeRunner(
            {"connection_show_active": _ok(stdout="Other:wifi:wlp1s0\n")}
        )
        service = VpnService(runner=runner, config=config)

        result = await service.disconnect("intetics")

        assert result == {
            "ok": True,
            "id": "intetics",
            "connection": "Intetics VPN",
            "connected": False,
            "device": None,
            "already_disconnected": True,
        }
        assert "connection_down" not in [call[0] for call in runner.calls]

    async def test_disconnect_fails(self, config: dict[str, VpnProfile]) -> None:
        runner = FakeRunner(
            {
                "connection_show_active": _ok(stdout="Intetics VPN:vpn:wg0\n"),
                "connection_down": _fail(
                    exit_code=1, stderr="Error: could not deactivate"
                ),
            }
        )
        service = VpnService(runner=runner, config=config)

        result = await service.disconnect("intetics")

        assert result["ok"] is False
        assert result["error"] == "disconnect_failed"
        assert result["stderr"] == "Error: could not deactivate"


class TestReconnect:
    """``reconnect`` is health-aware."""

    async def test_healthy_no_op(self, config: dict[str, VpnProfile]) -> None:
        runner = FakeRunner(
            {
                "general_status": _ok(),
                "connection_show": _ok(),
                "connection_show_active": _ok(stdout="Intetics VPN:vpn:wg0\n"),
                "route_get": _ok(
                    stdout="10.13.0.1 dev wg0 table 51820 src 10.13.0.5 uid 1000\n    cache"
                ),
            }
        )
        service = VpnService(runner=runner, config=config)

        result = await service.reconnect("intetics")

        assert result["ok"] is True
        assert result["reconnected"] is False
        assert result["usable"] is True
        assert "diagnose" in result
        assert "connection_down" not in [call[0] for call in runner.calls]
        assert "connection_up" not in [call[0] for call in runner.calls]

    async def test_unhealthy_rebuilds(self, config: dict[str, VpnProfile]) -> None:
        route_get_calls = [
            # first diagnose: both via eth0 -> unusable
            _ok(stdout="10.13.0.1 dev eth0 src 192.168.1.50"),
            _ok(stdout="10.12.0.1 dev eth0 src 192.168.1.50"),
            # after reconnect: both via wg0 -> usable
            _ok(stdout="10.13.0.1 dev wg0 src 10.99.0.2 uid 1000\n    cache"),
            _ok(stdout="10.12.0.1 dev wg0 src 10.99.0.2 uid 1000\n    cache"),
        ]

        async def route_response(_method: str, _args: tuple[Any, ...]) -> NmcliResult:
            return route_get_calls.pop(0)

        active_calls = [
            # initial diagnose
            "Intetics VPN:vpn:wg0\n",
            # disconnect pre-check
            "Intetics VPN:vpn:wg0\n",
            # post disconnect
            "Other:wifi:wlp1s0\n",
            # connect status: inactive before up call
            "Other:wifi:wlp1s0\n",
            # post connect verify
            "Intetics VPN:vpn:wg0\n",
            # final diagnose
            "Intetics VPN:vpn:wg0\n",
        ]

        async def active_response(_method: str, _args: tuple[Any, ...]) -> NmcliResult:
            return _ok(stdout=active_calls.pop(0))

        runner = FakeRunner(
            {
                "general_status": _ok(),
                "connection_show": _ok(),
                "connection_show_active": active_response,
                "route_get": route_response,
                "connection_down": _ok(),
                "connection_up": _ok(),
            }
        )
        service = VpnService(runner=runner, config=config)

        result = await service.reconnect("intetics")

        assert result["ok"] is True
        assert result["reconnected"] is True
        assert result["usable"] is True
        call_names = [call[0] for call in runner.calls]
        assert call_names.count("connection_down") == 1
        assert call_names.count("connection_up") == 1

    async def test_rebuild_still_unusable(self, config: dict[str, VpnProfile]) -> None:
        route_get_calls = [
            # first diagnose: both via eth0 -> unusable
            _ok(stdout="10.13.0.1 dev eth0 src 192.168.1.50"),
            _ok(stdout="10.12.0.1 dev eth0 src 192.168.1.50"),
            # after reconnect: both still via eth0 -> unusable
            _ok(stdout="10.13.0.1 dev eth0 src 192.168.1.50"),
            _ok(stdout="10.12.0.1 dev eth0 src 192.168.1.50"),
        ]

        async def route_response(_method: str, _args: tuple[Any, ...]) -> NmcliResult:
            return route_get_calls.pop(0)

        active_calls = [
            # initial diagnose
            "Intetics VPN:vpn:wg0\n",
            # disconnect pre-check
            "Intetics VPN:vpn:wg0\n",
            # post disconnect
            "Other:wifi:wlp1s0\n",
            # connect status: inactive before up call
            "Other:wifi:wlp1s0\n",
            # post connect verify
            "Intetics VPN:vpn:wg0\n",
            # final diagnose
            "Intetics VPN:vpn:wg0\n",
        ]

        async def active_response(_method: str, _args: tuple[Any, ...]) -> NmcliResult:
            return _ok(stdout=active_calls.pop(0))

        runner = FakeRunner(
            {
                "general_status": _ok(),
                "connection_show": _ok(),
                "connection_show_active": active_response,
                "route_get": route_response,
                "connection_down": _ok(),
                "connection_up": _ok(),
            }
        )
        service = VpnService(runner=runner, config=config)

        result = await service.reconnect("intetics")

        assert result["ok"] is False
        assert result["error"] == "reconnect_failed"
        assert result["reconnected"] is True

    async def test_reconnect_disconnect_fails(
        self, config: dict[str, VpnProfile]
    ) -> None:
        runner = FakeRunner(
            {
                "general_status": _ok(),
                "connection_show": _ok(),
                "connection_show_active": _ok(stdout="Intetics VPN:vpn:wg0\n"),
                "route_get": _ok(
                    stdout="10.13.0.1 dev eth0 src 192.168.1.50"
                ),
                "connection_down": _fail(exit_code=1, stderr="down failed"),
            }
        )
        service = VpnService(runner=runner, config=config)

        result = await service.reconnect("intetics")

        assert result["ok"] is False
        assert result["error"] == "reconnect_failed"
        assert result["stage"] == "disconnect"

    async def test_reconnect_connect_fails(
        self, config: dict[str, VpnProfile]
    ) -> None:
        active_calls = [
            # initial diagnose
            "Intetics VPN:vpn:wg0\n",
            # post disconnect
            "Other:wifi:wlp1s0\n",
            # post connect verify
            "Other:wifi:wlp1s0\n",
        ]

        async def active_response(_method: str, _args: tuple[Any, ...]) -> NmcliResult:
            return _ok(stdout=active_calls.pop(0))

        runner = FakeRunner(
            {
                "general_status": _ok(),
                "connection_show": _ok(),
                "connection_show_active": active_response,
                "route_get": _ok(
                    stdout="10.13.0.1 dev eth0 src 192.168.1.50"
                ),
                "connection_down": _ok(),
                "connection_up": _fail(exit_code=1, stderr="up failed"),
            }
        )
        service = VpnService(runner=runner, config=config)

        result = await service.reconnect("intetics")

        assert result["ok"] is False
        assert result["error"] == "reconnect_failed"
        assert result["stage"] == "connect"

    async def test_reconnect_from_disconnected(
        self, config: dict[str, VpnProfile]
    ) -> None:
        route_get_calls = [
            # initial diagnose: disconnected, routes skipped
            # final diagnose: both via wg0 -> usable
            _ok(stdout="10.13.0.1 dev wg0 src 10.99.0.2 uid 1000\n    cache"),
            _ok(stdout="10.12.0.1 dev wg0 src 10.99.0.2 uid 1000\n    cache"),
        ]

        async def route_response(_method: str, _args: tuple[Any, ...]) -> NmcliResult:
            return route_get_calls.pop(0)

        active_calls = [
            # initial diagnose: inactive
            "Other:wifi:wlp1s0\n",
            # disconnect pre-check: already inactive (no-op)
            "Other:wifi:wlp1s0\n",
            # connect status: inactive before up call
            "Other:wifi:wlp1s0\n",
            # post connect verify
            "Intetics VPN:vpn:wg0\n",
            # final diagnose
            "Intetics VPN:vpn:wg0\n",
        ]

        async def active_response(_method: str, _args: tuple[Any, ...]) -> NmcliResult:
            return _ok(stdout=active_calls.pop(0))

        runner = FakeRunner(
            {
                "general_status": _ok(),
                "connection_show": _ok(),
                "connection_show_active": active_response,
                "route_get": route_response,
                "connection_up": _ok(),
            }
        )
        service = VpnService(runner=runner, config=config)

        result = await service.reconnect("intetics")

        assert result["ok"] is True
        assert result["reconnected"] is True
        assert result["usable"] is True
        call_names = [call[0] for call in runner.calls]
        assert call_names.count("connection_down") == 0
        assert call_names.count("connection_up") == 1

    async def test_reconnect_from_disconnected_up_fails(
        self, config: dict[str, VpnProfile]
    ) -> None:
        active_calls = [
            # initial diagnose: inactive
            "Other:wifi:wlp1s0\n",
            # disconnect pre-check: already inactive (no-op)
            "Other:wifi:wlp1s0\n",
            # post connect verify
            "Other:wifi:wlp1s0\n",
        ]

        async def active_response(_method: str, _args: tuple[Any, ...]) -> NmcliResult:
            return _ok(stdout=active_calls.pop(0))

        runner = FakeRunner(
            {
                "general_status": _ok(),
                "connection_show": _ok(),
                "connection_show_active": active_response,
                "connection_up": _fail(exit_code=1, stderr="up failed"),
            }
        )
        service = VpnService(runner=runner, config=config)

        result = await service.reconnect("intetics")

        assert result["ok"] is False
        assert result["error"] == "reconnect_failed"
        assert result["stage"] == "connect"
        assert result["reconnected"] is False


class TestDiagnose:
    """``diagnose`` local usability chain."""

    async def test_fully_usable(self, config: dict[str, VpnProfile]) -> None:
        route_get_calls = [
            _ok(stdout="10.13.0.1 dev wg0 table 51820 src 10.99.0.2 uid 1000\n    cache"),
            _ok(stdout="10.12.0.1 dev wg0 table 51820 src 10.99.0.2 uid 1000\n    cache"),
        ]

        async def route_response(_method: str, _args: tuple[Any, ...]) -> NmcliResult:
            return route_get_calls.pop(0)

        runner = FakeRunner(
            {
                "general_status": _ok(),
                "connection_show": _ok(),
                "connection_show_active": _ok(stdout="Intetics VPN:vpn:wg0\n"),
                "route_get": route_response,
            }
        )
        service = VpnService(runner=runner, config=config)

        result = await service.diagnose("intetics")

        assert result["ok"] is True
        assert result["usable"] is True
        assert result["connected"] is True
        assert result["interface"] == "wg0"
        assert len(result["routes"]) == 2
        for route in result["routes"]:
            assert route["ok"] is True
            assert route["resolved_interface"] == "wg0"
            assert "detail" in route

    async def test_route_missing_via_default_interface(
        self, config: dict[str, VpnProfile]
    ) -> None:
        route_get_calls = [
            _ok(stdout="10.13.0.1 dev wg0 table 51820 src 10.99.0.2 uid 1000\n    cache"),
            _ok(stdout="10.12.0.1 dev eth0 src 192.168.1.50"),
        ]

        async def route_response(_method: str, _args: tuple[Any, ...]) -> NmcliResult:
            return route_get_calls.pop(0)

        runner = FakeRunner(
            {
                "general_status": _ok(),
                "connection_show": _ok(),
                "connection_show_active": _ok(stdout="Intetics VPN:vpn:wg0\n"),
                "route_get": route_response,
            }
        )
        service = VpnService(runner=runner, config=config)

        result = await service.diagnose("intetics")

        assert result["ok"] is True
        assert result["usable"] is False
        assert result["connected"] is True
        assert result["interface"] == "wg0"
        route_checks = result["routes"]
        assert route_checks[0]["ok"] is True
        assert route_checks[1]["ok"] is False
        assert route_checks[1]["resolved_interface"] == "eth0"

    async def test_disconnected(self, config: dict[str, VpnProfile]) -> None:
        runner = FakeRunner(
            {
                "general_status": _ok(),
                "connection_show": _ok(),
                "connection_show_active": _ok(stdout="Other:wifi:wlp1s0\n"),
            }
        )
        service = VpnService(runner=runner, config=config)

        result = await service.diagnose("intetics")

        assert result["ok"] is True
        assert result["connected"] is False
        assert result["interface"] is None
        assert result["usable"] is False
        assert result["routes"] == []

    async def test_nm_unresponsive(self, config: dict[str, VpnProfile]) -> None:
        runner = FakeRunner(
            {
                "general_status": _fail(
                    exit_code=8, stderr="Error: NetworkManager is not running"
                )
            }
        )
        service = VpnService(runner=runner, config=config)

        result = await service.diagnose("intetics")

        assert result["ok"] is False
        assert result["error"] == "nm_unresponsive"

    async def test_route_get_timeout(self, config: dict[str, VpnProfile]) -> None:
        runner = FakeRunner(
            {
                "general_status": _ok(),
                "connection_show": _ok(),
                "connection_show_active": _ok(stdout="Intetics VPN:vpn:wg0\n"),
                "route_get": _timeout(),
            }
        )
        service = VpnService(runner=runner, config=config)

        result = await service.diagnose("intetics")

        assert result["ok"] is True
        assert result["usable"] is False
        assert len(result["routes"]) == 2
        for route in result["routes"]:
            assert route["ok"] is False
            assert route["resolved_interface"] is None
            assert route["detail"] == "route lookup timed out"

    async def test_connection_show_timeout_not_misreported_as_missing(
        self, config: dict[str, VpnProfile]
    ) -> None:
        runner = FakeRunner(
            {
                "general_status": _ok(),
                "connection_show": _timeout(),
            }
        )
        service = VpnService(runner=runner, config=config)

        result = await service.diagnose("intetics")

        assert result["ok"] is False
        assert result["error"] == "timeout"
        assert result["timed_out"] is True
        assert result["exit_code"] is None
        assert result["detail"] == "nmcli connection show timed out"
        assert result.get("profile_exists") is not False
        assert "message" not in result

    async def test_connection_show_spawn_failed_not_misreported_as_missing(
        self, config: dict[str, VpnProfile]
    ) -> None:
        runner = FakeRunner(
            {
                "general_status": _ok(),
                "connection_show": NmcliResult(
                    ok=False,
                    exit_code=None,
                    stdout="",
                    stderr="",
                    error="spawn_failed",
                    argv=("nmcli", "connection", "show", "Intetics VPN"),
                ),
            }
        )
        service = VpnService(runner=runner, config=config)

        result = await service.diagnose("intetics")

        assert result["ok"] is False
        assert result["error"] == "nmcli_failed"
        assert result["timed_out"] is False
        assert result["detail"] == "nmcli connection show could not be started"
        assert result.get("profile_exists") is not False
        assert "message" not in result

    async def test_connection_show_clean_nonzero_means_missing(
        self, config: dict[str, VpnProfile]
    ) -> None:
        runner = FakeRunner(
            {
                "general_status": _ok(),
                "connection_show": _fail(
                    exit_code=10, stderr="Error: Intetics VPN - no such connection"
                ),
            }
        )
        service = VpnService(runner=runner, config=config)

        result = await service.diagnose("intetics")

        assert result["ok"] is True
        assert result["usable"] is False
        assert result["checks"]["profile_exists"] is False
        assert result["message"] == "profile does not exist in NetworkManager"


class TestNetworkStatus:
    """``network_status`` parses device rows."""

    async def test_devices_listed(self, config: dict[str, VpnProfile]) -> None:
        runner = FakeRunner(
            {
                "device_status": _ok(
                    stdout="eth0:ethernet:connected:Wired\nwg0:wireguard:connected:Intetics VPN\n"
                )
            }
        )
        service = VpnService(runner=runner, config=config)

        result = await service.network_status()

        assert result == {
            "ok": True,
            "devices": [
                {
                    "device": "eth0",
                    "type": "ethernet",
                    "state": "connected",
                    "connection": "Wired",
                },
                {
                    "device": "wg0",
                    "type": "wireguard",
                    "state": "connected",
                    "connection": "Intetics VPN",
                },
            ],
        }

    async def test_device_status_failure(self, config: dict[str, VpnProfile]) -> None:
        runner = FakeRunner(
            {"device_status": _fail(exit_code=1, stderr="Error: nmcli failed")}
        )
        service = VpnService(runner=runner, config=config)

        result = await service.network_status()

        assert result["ok"] is False
        assert result["error"] == "nmcli_failed"


class TestUnknownId:
    """Every name-taking tool returns a structured error for unknown ids."""

    @pytest.fixture
    def unknown_service(self, config: dict[str, VpnProfile]) -> VpnService:
        return VpnService(runner=FakeRunner({}), config=config)

    @pytest.mark.parametrize(
        "method",
        ["status", "connect", "disconnect", "reconnect", "diagnose"],
    )
    async def test_unknown_id_error(
        self, unknown_service: VpnService, method: str
    ) -> None:
        result = await getattr(unknown_service, method)("no-such-vpn")

        assert result == {
            "ok": False,
            "error": "unknown_id",
            "message": "unknown VPN id 'no-such-vpn'",
            "available_ids": ["intetics", "testvpn"],
        }

    async def test_list_vpns_never_takes_name(self, unknown_service: VpnService) -> None:
        assert "name" not in unknown_service.list_vpns.__code__.co_varnames


class TestJsonSerializable:
    """Every public tool result round-trips through JSON."""

    @pytest.mark.parametrize(
        "method,args",
        [
            ("list_vpns", []),
            ("status", ["intetics"]),
            ("connect", ["intetics"]),
            ("disconnect", ["intetics"]),
            ("reconnect", ["intetics"]),
            ("diagnose", ["intetics"]),
            ("network_status", []),
        ],
    )
    async def test_all_results_are_json_serializable(
        self, service: VpnService, method: str, args: list[Any]
    ) -> None:
        result = await getattr(service, method)(*args)

        serialized = json.dumps(result)
        assert isinstance(serialized, str)
        assert "ok" in result
        assert result["ok"] in (True, False)
