"""MCP server layer for the nmcli MCP server.

This module exposes exactly seven MCP tools that delegate to
:class:`nmcli_mcp.vpn.VpnService`.  Tool functions are plain async functions
covered by unit tests; the module-level :data:`mcp` instance is what the
``nmcli-mcp`` console script runs via :func:`main`.

Design constraints honoured here:

* stdout is reserved for the MCP protocol; all logging/diagnostics go to
  stderr.
* The only LLM-supplied input to any VPN-addressing tool is a VPN ``id`` from
  server config (``name`` parameter); connection names are resolved internally.
* Tool results are plain ``dict[str, Any]`` JSON-serializable objects with an
  ``ok`` boolean field.
* Python 3.10 discipline: ``from __future__ import annotations``, no
  ``asyncio.timeout()``, no ``typing.Self``.

Important: the installed ``mcp`` package is version 2.x, where the v1
``FastMCP`` class was renamed to :class:`mcp.server.mcpserver.MCPServer`.
This module uses the v2 API; tools are decorated with ``@mcp.tool()`` and
return ``dict[str, Any]`` which the SDK validates as structured output.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from mcp.server.mcpserver import MCPServer

from nmcli_mcp.config import ConfigError, VpnConfig, load_config
from nmcli_mcp.nmcli import NmcliRunner
from nmcli_mcp.vpn import VpnService

#: The single MCPServer instance used by the console script and tests.
#:
#: Tools are registered by decorating plain async functions below.  Tests can
#: introspect tools via ``await mcp.list_tools()`` and call tool functions
#: directly.
mcp = MCPServer("nmcli")

#: Module-level service state.  Set by :func:`init_service`; ``None`` until
#: initialization.  This indirection makes the MCP layer testable without real
#: subprocesses.
_service: VpnService | None = None

logger = logging.getLogger("nmcli_mcp.server")


def init_service(config: VpnConfig, runner: NmcliRunner | None = None) -> None:
    """Initialize the module-level VPN service.

    Args:
        config: Validated VPN configuration mapping.
        runner: Optional nmcli runner.  If omitted, a default
            :class:`NmcliRunner` is constructed (used in production).
    """

    global _service
    _service = VpnService(runner or NmcliRunner(), config)


def _get_service() -> VpnService:
    """Return the initialized service or raise a clear error."""

    if _service is None:
        raise RuntimeError("service not initialized; call init_service() first")
    return _service


@mcp.tool()
async def vpn_list() -> dict[str, Any]:
    """List the VPN profiles configured on this server.

    Returns a structured list of configured VPN ids and their corresponding
    NetworkManager connection names.  This tool does not invoke any subprocess.
    """

    return await _get_service().list_vpns()


@mcp.tool()
async def vpn_status(name: str) -> dict[str, Any]:
    """Report whether a VPN is active and which tunnel device it uses.

    Args:
        name: A VPN id from the server config (not a NetworkManager
            connection name).  Unknown ids return a structured error listing
            the available ids.
    """

    return await _get_service().status(name)


@mcp.tool()
async def vpn_diagnose(name: str) -> dict[str, Any]:
    """Diagnose local usability of a VPN without probing remote endpoints.

    The result includes per-route detail.  ``usable`` means locally routed:
    the connection is active, a tunnel interface is bound, and every configured
    expected route resolves through that interface via ``ip route get``.  This
    is NOT a guarantee of end-to-end reachability; the agent verifies
    reachability with its own tools.

    Args:
        name: A VPN id from the server config (not a NetworkManager
            connection name).  Unknown ids return a structured error listing
            the available ids.
    """

    return await _get_service().diagnose(name)


@mcp.tool()
async def network_status() -> dict[str, Any]:
    """Return an overview of NetworkManager devices.

    Each row contains the device name, type, state, and the connection bound to
    it, parsed from ``nmcli device status``.
    """

    return await _get_service().network_status()


@mcp.tool()
async def vpn_connect(name: str) -> dict[str, Any]:
    """Activate a VPN connection idempotently.

    If the connection is already active, it is left untouched and the current
    state is returned as success.  Otherwise ``nmcli connection up`` is invoked
    for the connection mapped to ``name`` and the active state is re-verified.

    Args:
        name: A VPN id from the server config (not a NetworkManager
            connection name).  Unknown ids return a structured error listing
            the available ids.
    """

    logger.info("vpn_connect requested for %r", name)
    return await _get_service().connect(name)


@mcp.tool()
async def vpn_disconnect(name: str) -> dict[str, Any]:
    """Deactivate a VPN connection and report the verified post-state.

    Args:
        name: A VPN id from the server config (not a NetworkManager
            connection name).  Unknown ids return a structured error listing
            the available ids.
    """

    logger.info("vpn_disconnect requested for %r", name)
    return await _get_service().disconnect(name)


@mcp.tool()
async def vpn_reconnect(name: str) -> dict[str, Any]:
    """Health-aware reconnect: diagnose first, rebuild only when needed.

    First runs a local diagnosis.  If the VPN is already usable, no state change
    occurs and the current diagnosis is returned.  Otherwise the connection is
    disconnected, connected, and re-diagnosed; the final state is returned.

    Args:
        name: A VPN id from the server config (not a NetworkManager
            connection name).  Unknown ids return a structured error listing
            the available ids.
    """

    logger.info("vpn_reconnect requested for %r", name)
    return await _get_service().reconnect(name)


def main() -> None:
    """Console-script entry point for the ``nmcli-mcp`` command.

    Loads config (fail-fast on :class:`ConfigError`), initializes the VPN
    service, logs the number of configured profiles, and runs the MCP server
    over stdio.
    """

    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"nmcli-mcp: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    init_service(config)
    logger.info("nmcli-mcp starting with %d VPN profile(s)", len(config))
    mcp.run(transport="stdio")
