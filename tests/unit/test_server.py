"""Unit tests for ``nmcli_mcp.server``.

These tests exercise the MCP layer without invoking real ``nmcli`` or ``ip``
subprocesses.  A programmable ``FakeRunner`` substitutes for
:class:`NmcliRunner`, and tests call the decorated tool functions directly.
"""

from __future__ import annotations

import ipaddress
import json
import textwrap
from pathlib import Path
from typing import Any

import pytest

from nmcli_mcp.config import VpnProfile
from nmcli_mcp.nmcli import NmcliResult, NmcliRunner
from nmcli_mcp.server import (
    init_service,
    main,
    mcp,
    network_status,
    vpn_connect,
    vpn_diagnose,
    vpn_disconnect,
    vpn_list,
    vpn_reconnect,
    vpn_status,
)

from ._doubles import Call, FakeRunner, _fail, _ok


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
def runner(config: dict[str, VpnProfile]) -> FakeRunner:
    """A fake runner with a happy-path response for every tool."""

    return FakeRunner(
        responses={
            "general_status": _ok(
                stdout=(
                    "connected\n"
                    "STATE\tCONNECTED\n"
                )
            ),
            "connection_show": _ok(
                stdout="connection.id:\tintetics-vpn-uuid\n"
            ),
            "connection_show_active": _ok(
                stdout="Intetics VPN:vpn:wg0\n"
                "Test VPN:vpn:tun0\n"
                "Wi-Fi:wifi:wlp1s0\n"
            ),
            "device_status": _ok(
                stdout="wg0:vpn:connected:Intetics VPN\n"
                "wlp1s0:wifi:connected:Wi-Fi\n"
            ),
            "connection_up": _ok(stdout="Connection successfully activated\n"),
            "connection_down": _ok(stdout="Connection 'Intetics VPN' deactivated.\n"),
            "route_get": _ok(
                stdout="10.13.0.1 dev wg0 table 51820 src 10.13.13.2 uid 1000\n"
            ),
        }
    )


@pytest.fixture
def init(runner: FakeRunner, config: dict[str, VpnProfile]) -> None:
    """Initialize the module-level service with the fake runner and config."""

    init_service(config, runner)


EXPECTED_TOOL_NAMES = {
    "vpn_list",
    "vpn_status",
    "vpn_diagnose",
    "network_status",
    "vpn_connect",
    "vpn_disconnect",
    "vpn_reconnect",
}


class TestToolRegistration:
    """The MCPServer instance exposes exactly the seven expected tools."""

    async def test_exact_tool_names(self) -> None:
        tools = await mcp.list_tools()
        names = {tool.name for tool in tools}

        assert names == EXPECTED_TOOL_NAMES


class ToolInvocationMixin:
    """Shared helpers for exercising the seven tool functions."""

    async def _assert_ok_dict(self, result: dict[str, Any]) -> None:
        assert isinstance(result, dict)
        assert "ok" in result
        assert isinstance(result["ok"], bool)
        assert json.dumps(result) is not None


class TestVpnList(ToolInvocationMixin):
    """``vpn_list`` returns configured profiles without subprocess calls."""

    async def test_returns_profiles(self, init: None) -> None:
        result = await vpn_list()

        await self._assert_ok_dict(result)
        assert result["ok"] is True
        assert result["vpns"] == [
            {"id": "intetics", "connection": "Intetics VPN"},
            {"id": "testvpn", "connection": "Test VPN"},
        ]


class TestVpnStatus(ToolInvocationMixin):
    """``vpn_status`` reports active/inactive state and bound device."""

    async def test_active_vpn(self, init: None) -> None:
        result = await vpn_status("intetics")

        await self._assert_ok_dict(result)
        assert result["ok"] is True
        assert result["id"] == "intetics"
        assert result["connected"] is True
        assert result["device"] == "wg0"

    async def test_inactive_vpn(self, runner: FakeRunner, config: dict[str, VpnProfile]) -> None:
        init_service(config, FakeRunner({"connection_show_active": _ok(stdout="Other:wifi:wlp1s0\n")}))
        result = await vpn_status("intetics")

        await self._assert_ok_dict(result)
        assert result["ok"] is True
        assert result["connected"] is False
        assert result["device"] is None


class TestVpnDiagnose(ToolInvocationMixin):
    """``vpn_diagnose`` returns local-usability detail."""

    async def test_usable(self, init: None) -> None:
        result = await vpn_diagnose("intetics")

        await self._assert_ok_dict(result)
        assert result["ok"] is True
        assert result["id"] == "intetics"
        assert result["connected"] is True
        assert result["interface"] == "wg0"
        assert result["usable"] is True
        assert all(route["ok"] for route in result["routes"])

    async def test_nm_unresponsive(self, runner: FakeRunner, config: dict[str, VpnProfile]) -> None:
        init_service(config, FakeRunner({"general_status": _fail()}))
        result = await vpn_diagnose("intetics")

        await self._assert_ok_dict(result)
        assert result["ok"] is False
        assert result["error"] == "nm_unresponsive"


class TestNetworkStatus(ToolInvocationMixin):
    """``network_status`` lists NetworkManager devices."""

    async def test_lists_devices(self, init: None) -> None:
        result = await network_status()

        await self._assert_ok_dict(result)
        assert result["ok"] is True
        assert any(device["device"] == "wg0" for device in result["devices"])


class TestVpnConnect(ToolInvocationMixin):
    """``vpn_connect`` is idempotent."""

    async def test_already_active(self, init: None) -> None:
        result = await vpn_connect("intetics")

        await self._assert_ok_dict(result)
        assert result["ok"] is True
        assert result["id"] == "intetics"
        assert result["connected"] is True
        assert result["already_connected"] is True

    async def test_activates_when_inactive(
        self, config: dict[str, VpnProfile]
    ) -> None:
        calls: list[Call] = []

        async def _respond(method: str, args: tuple[Any, ...]) -> NmcliResult:
            calls.append((method, args))
            if method == "connection_show_active":
                # First call: inactive; second call (post-activation): active.
                if len([c for c in calls if c[0] == "connection_show_active"]) == 1:
                    return _ok(stdout="Other:wifi:wlp1s0\n")
                return _ok(stdout="Intetics VPN:vpn:wg0\n")
            if method == "connection_up":
                return _ok(stdout="Connection successfully activated\n")
            return _ok()

        fake = FakeRunner(responses={"connection_show_active": _respond, "connection_up": _respond})
        init_service(config, fake)

        result = await vpn_connect("intetics")

        await self._assert_ok_dict(result)
        assert result["ok"] is True
        assert result["connected"] is True
        assert result["already_connected"] is False
        assert any(call == ("connection_up", ("Intetics VPN",)) for call in fake.calls)


class TestVpnDisconnect(ToolInvocationMixin):
    """``vpn_disconnect`` deactivates a VPN."""

    async def test_disconnects(self, runner: FakeRunner, init: None) -> None:
        # The VPN starts active; the disconnect flow status-checks first,
        # tears down, then re-verifies, so the active list must reflect the
        # state change rather than return a static snapshot.
        state = {"intetics_active": True}

        async def active_list(method: str, args: tuple[Any, ...]) -> NmcliResult:
            active = "Intetics VPN:vpn:wg0\n" if state["intetics_active"] else ""
            return _ok(stdout=active)

        async def on_down(method: str, args: tuple[Any, ...]) -> NmcliResult:
            state["intetics_active"] = False
            return _ok(stdout="Connection 'Intetics VPN' deactivated.\n")

        runner.responses = {**runner.responses, "connection_show_active": active_list, "connection_down": on_down}

        result = await vpn_disconnect("intetics")

        await self._assert_ok_dict(result)
        assert result["ok"] is True
        assert result["id"] == "intetics"
        assert result["connected"] is False
        assert result["already_disconnected"] is False
        assert ("connection_down", ("Intetics VPN",)) in runner.calls


class TestVpnReconnect(ToolInvocationMixin):
    """``vpn_reconnect`` is health-aware."""

    async def test_healthy_noop(self, init: None) -> None:
        result = await vpn_reconnect("intetics")

        await self._assert_ok_dict(result)
        assert result["ok"] is True
        assert result["reconnected"] is False
        assert result["usable"] is True


class TestUnknownId:
    """Unknown ids return the expected structured error shape."""

    async def test_name_taking_tools(self, init: None) -> None:
        for tool in (vpn_status, vpn_diagnose, vpn_connect, vpn_disconnect, vpn_reconnect):
            result = await tool("not-a-vpn")

            assert result == {
                "ok": False,
                "error": "unknown_id",
                "message": "unknown VPN id 'not-a-vpn'",
                "available_ids": ["intetics", "testvpn"],
            }


class TestJsonSerialization:
    """Every happy-path and failure tool result is JSON-serializable."""

    async def test_all_tools_round_trip(self, init: None) -> None:
        results = [
            await vpn_list(),
            await vpn_status("intetics"),
            await vpn_diagnose("intetics"),
            await network_status(),
            await vpn_connect("intetics"),
            await vpn_disconnect("intetics"),
            await vpn_reconnect("intetics"),
        ]
        for result in results:
            serialized = json.dumps(result)
            deserialized = json.loads(serialized)
            assert isinstance(deserialized["ok"], bool)


class TestStdoutCleanliness:
    """No tool path writes to stdout."""

    async def test_all_tools_keep_stdout_empty(
        self, init: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        await vpn_list()
        await vpn_status("intetics")
        await vpn_diagnose("intetics")
        await network_status()
        await vpn_connect("intetics")
        await vpn_disconnect("intetics")
        await vpn_reconnect("intetics")
        await vpn_status("unknown-vpn")

        captured = capsys.readouterr()
        assert captured.out == ""


class TestMain:
    """``main()`` fail-fast and success behavior."""

    def test_fail_fast_on_missing_config(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Missing config raises SystemExit(1) and writes the error to stderr."""

        monkeypatch.setenv("NMCLI_MCP_CONFIG", str(tmp_path / "env-config.toml"))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        monkeypatch.delenv("NMCLI_MCP_CONFIG", raising=False)
        # Actually set the env vars to nonexistent paths:
        for env_name in ("NMCLI_MCP_CONFIG", "XDG_CONFIG_HOME", "HOME"):
            monkeypatch.delenv(env_name, raising=False)

        # Point every candidate at a non-existent temp tree.
        empty_root = tmp_path / "empty"
        empty_root.mkdir()
        monkeypatch.setenv("NMCLI_MCP_CONFIG", str(empty_root / "env-config.toml"))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(empty_root / "xdg"))
        monkeypatch.setenv("HOME", str(empty_root / "home"))

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "nmcli-mcp:" in captured.err

    async def test_success_path_initializes_service(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Valid config initializes the service and runs the server over stdio."""

        config_path = tmp_path / "nmcli-mcp-server" / "config.toml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text(
            textwrap.dedent(
                """
                [[vpn]]
                id = "tmpvpn"
                connection = "Temporary VPN"
                expected_routes = ["10.99.0.0/16"]
                """
            )
        )
        monkeypatch.setenv("NMCLI_MCP_CONFIG", str(config_path))

        run_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

        def fake_run(*args: Any, **kwargs: Any) -> None:
            run_calls.append((args, kwargs))

        monkeypatch.setattr(mcp, "run", fake_run)

        main()

        assert len(run_calls) == 1
        _args, kwargs = run_calls[0]
        assert kwargs.get("transport") == "stdio"

        result = await vpn_list()
        assert result["ok"] is True
        assert result["vpns"] == [{"id": "tmpvpn", "connection": "Temporary VPN"}]


class TestInitService:
    """``init_service`` accepts a runner and defaults to a real one."""

    def test_default_runner(self, config: dict[str, VpnProfile]) -> None:
        init_service(config)

        # Service state was set; runner is a real NmcliRunner.
        from nmcli_mcp.server import _service

        assert _service is not None
        assert isinstance(_service.runner, NmcliRunner)
