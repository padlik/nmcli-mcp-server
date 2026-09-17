"""Integration tests for the nmcli MCP server VPN tools.

These tests exercise real NetworkManager state through the public
:class:`~nmcli_mcp.vpn.VpnService` API. They are designed to run inside the
``nsjail-test`` Lima VM described in ``openspec/changes/mcp-server-mvp/design.md``
(decision 7). The VM is provisioned with NetworkManager and a fake WireGuard
connection named "Test VPN" by:

* ``scripts/lima_setup.sh``  - installs NetworkManager/WireGuard and configures
  NM not to manage the VM's default interface.
* ``scripts/lima_fixture.sh`` - creates the "Test VPN" connection and the fixture
  config used by this suite.

Expected fixture config (``$HOME/nmcli-mcp-fixture/config.toml`` in the VM) ::

    [[vpn]]
    id = "testvpn"
    connection = "Test VPN"
    expected_routes = ["10.13.0.0/16", "10.12.0.0/16", "10.8.0.5/32"]

Run in the VM after ``uv sync``::

    export NMCLI_MCP_CONFIG="$HOME/nmcli-mcp-fixture/config.toml"
    uv run pytest -m integration

On macOS the tests are skipped cleanly because ``nmcli`` and ``ip`` are not
available.
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Iterator
from typing import Any

import pytest

from nmcli_mcp.config import load_config
from nmcli_mcp.nmcli import NmcliRunner
from nmcli_mcp.vpn import VpnService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("nmcli") is None or shutil.which("ip") is None,
        reason="requires a Linux host with NetworkManager and iproute2",
    ),
]


@pytest.fixture(scope="module")
def real_service() -> VpnService:
    """Build a :class:`VpnService` from the fixture config.

    The orchestrator sets ``NMCLI_MCP_CONFIG`` before invoking pytest. This
    fixture fails with a clear message if the environment is not configured.
    """

    config = load_config()
    assert "testvpn" in config, (
        "fixture config must contain a VPN with id 'testvpn'; "
        "set NMCLI_MCP_CONFIG to the fixture config path "
        "(e.g. $HOME/nmcli-mcp-fixture/config.toml)"
    )
    return VpnService(NmcliRunner(), config)


def _tools_available() -> bool:
    """Return True when both ``nmcli`` and ``ip`` are present on PATH."""

    return shutil.which("nmcli") is not None and shutil.which("ip") is not None


async def _disconnect_and_settle(service: VpnService) -> None:
    """Disconnect the test VPN and wait for NetworkManager state to settle."""

    await service.disconnect("testvpn")
    await asyncio.sleep(1)


@pytest.fixture(scope="session", autouse=True)
def ensure_test_vpn_disconnected() -> Iterator[None]:
    """Start and end the session with "Test VPN" disconnected.

    The fixture config must already exist at session setup time. A fresh
    disconnect and short settle are performed before the first test, and a final
    disconnect runs at teardown to leave the Lima rig clean.

    On hosts without ``nmcli``/``ip`` (e.g. macOS) the fixture is a no-op so the
    tests can skip cleanly.

    This fixture is deliberately synchronous and drives the async service via
    ``asyncio.run()``: session-scoped async fixtures are not handled by the
    configured pytest-asyncio auto mode in this environment (verified
    empirically), so a sync fixture is the reliable way to get true
    session-scoped setup/teardown while still calling async service code.
    """

    if not _tools_available():
        yield
        return

    config = load_config()
    assert "testvpn" in config, (
        "fixture config must contain a VPN with id 'testvpn'; "
        "set NMCLI_MCP_CONFIG to the fixture config path "
        "(e.g. $HOME/nmcli-mcp-fixture/config.toml)"
    )
    service = VpnService(NmcliRunner(), config)
    asyncio.run(_disconnect_and_settle(service))
    yield
    asyncio.run(service.disconnect("testvpn"))


async def _assert_state_after_connect(result: dict[str, Any]) -> None:
    """Common assertions for a successful connect response."""

    assert result["ok"] is True
    assert result["id"] == "testvpn"
    assert result["connection"] == "Test VPN"
    assert result["connected"] is True
    assert result["device"] == "wg0"


async def test_vpn_list_matches_fixture_config(real_service: VpnService) -> None:
    """`vpn_list` returns the single configured profile."""

    result = await real_service.list_vpns()

    assert result["ok"] is True
    assert result["vpns"] == [{"id": "testvpn", "connection": "Test VPN"}]


async def test_vpn_status_reports_disconnected_when_inactive(
    real_service: VpnService,
) -> None:
    """After ensuring the VPN is down, status reports disconnected."""

    await real_service.disconnect("testvpn")

    result = await real_service.status("testvpn")

    assert result["ok"] is True
    assert result["id"] == "testvpn"
    assert result["connection"] == "Test VPN"
    assert result["connected"] is False
    assert result["device"] is None


async def test_vpn_connect_activates_and_is_idempotent(
    real_service: VpnService,
) -> None:
    """First connect activates; second connect reports already connected."""

    first = await real_service.connect("testvpn")
    await _assert_state_after_connect(first)
    assert first["already_connected"] is False

    second = await real_service.connect("testvpn")
    await _assert_state_after_connect(second)
    assert second["already_connected"] is True


async def test_vpn_diagnose_usable_through_wg0(real_service: VpnService) -> None:
    """When up, diagnose reports usable with all expected routes via wg0."""

    await real_service.connect("testvpn")

    result = await real_service.diagnose("testvpn")

    assert result["ok"] is True
    assert result["id"] == "testvpn"
    assert result["connection"] == "Test VPN"
    assert result["connected"] is True
    assert result["interface"] == "wg0"
    assert result["usable"] is True

    checks = result["checks"]
    assert checks["nm_responsive"] is True
    assert checks["profile_exists"] is True
    assert checks["connected"] is True
    assert checks["interface"] == "wg0"

    routes = result["routes"]
    assert len(routes) == 3, f"expected 3 route entries, got {len(routes)}"

    expected_probes = ["10.13.0.1", "10.12.0.1", "10.8.0.5"]
    for entry, probe in zip(routes, expected_probes):
        assert entry["ok"] is True
        assert entry["resolved_interface"] == "wg0"
        assert entry["probe"] == probe


async def test_network_status_lists_devices(real_service: VpnService) -> None:
    """`network_status` includes a wg0 row bound to "Test VPN"."""

    await real_service.connect("testvpn")

    result = await real_service.network_status()

    assert result["ok"] is True
    devices = result["devices"]
    assert isinstance(devices, list)

    wg0_rows = [row for row in devices if row.get("device") == "wg0"]
    assert wg0_rows, "no device row for wg0 found in network_status output"

    wg0 = wg0_rows[0]
    assert wg0["connection"] == "Test VPN"
    assert "connected" in wg0["state"].lower()


async def test_vpn_disconnect_deactivates(real_service: VpnService) -> None:
    """Disconnect brings the VPN down and reports it was not already down."""

    await real_service.connect("testvpn")

    result = await real_service.disconnect("testvpn")

    assert result["ok"] is True
    assert result["id"] == "testvpn"
    assert result["connection"] == "Test VPN"
    assert result["connected"] is False
    assert result["already_disconnected"] is False

    status_after = await real_service.status("testvpn")
    assert status_after["ok"] is True
    assert status_after["connected"] is False
    assert status_after["device"] is None


async def test_vpn_reconnect_from_disconnected_brings_vpn_up(
    real_service: VpnService,
) -> None:
    """Reconnect from an inactive state activates and reports usable."""

    await real_service.disconnect("testvpn")
    await asyncio.sleep(1)

    result = await real_service.reconnect("testvpn")

    assert result["ok"] is True
    assert result["id"] == "testvpn"
    assert result["connection"] == "Test VPN"
    assert result["reconnected"] is True
    assert result["usable"] is True

    diagnose = result["diagnose"]
    assert diagnose["ok"] is True
    assert diagnose["connected"] is True
    assert diagnose["interface"] == "wg0"
    assert diagnose["usable"] is True

    status_after = await real_service.status("testvpn")
    assert status_after["ok"] is True
    assert status_after["connected"] is True
    assert status_after["device"] == "wg0"


async def test_vpn_reconnect_healthy_is_a_noop(real_service: VpnService) -> None:
    """Reconnect on an already-usable VPN is a no-op and keeps it usable."""

    await real_service.connect("testvpn")

    result = await real_service.reconnect("testvpn")

    assert result["ok"] is True
    assert result["id"] == "testvpn"
    assert result["connection"] == "Test VPN"
    assert result["reconnected"] is False
    assert result["usable"] is True

    diagnose = result["diagnose"]
    assert diagnose["ok"] is True
    assert diagnose["connected"] is True
    assert diagnose["interface"] == "wg0"
    assert diagnose["usable"] is True

    status_after = await real_service.status("testvpn")
    assert status_after["ok"] is True
    assert status_after["connected"] is True
    assert status_after["device"] == "wg0"
