"""VPN orchestration layer for the nmcli MCP server.

This module contains :class:`VpnService`, the orchestration class that the
MCP tool layer calls. It composes :class:`nmcli_mcp.nmcli.NmcliRunner` with
the loaded :class:`nmcli_mcp.config.VpnConfig` to implement status, diagnosis,
idempotent connect/disconnect, health-aware reconnect, and device overview.

All public methods return plain JSON-serializable dictionaries with an ``ok``
field. No exceptions leak to callers and no logging or printing happens here.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from nmcli_mcp.config import VpnConfig, VpnProfile
from nmcli_mcp.nmcli import (
    NmcliResult,
    NmcliRunner,
    parse_active_connections,
    parse_device_status,
)
from nmcli_mcp.routing import derive_probe, evaluate_route


class VpnService:
    """Orchestrate VPN state using a mockable nmcli runner and config.

    Attributes:
        runner: The :class:`NmcliRunner` used for subprocess calls.
        config: Mapping from VPN id to :class:`VpnProfile`.
    """

    def __init__(self, runner: NmcliRunner, config: VpnConfig) -> None:
        """Initialize the service.

        Args:
            runner: nmcli/ip subprocess runner.
            config: Validated VPN configuration mapping.
        """

        self.runner = runner
        self.config = config

    def _sorted_ids(self) -> list[str]:
        """Return configured ids in deterministic sorted order."""

        return sorted(self.config.keys())

    def _require_profile(self, name: str) -> VpnProfile | dict[str, Any]:
        """Resolve ``name`` to a profile or a structured unknown-id error."""

        profile = self.config.get(name)
        if profile is not None:
            return profile
        return {
            "ok": False,
            "error": "unknown_id",
            "message": f"unknown VPN id {name!r}",
            "available_ids": self._sorted_ids(),
        }

    @staticmethod
    def _device_or_none(device: str) -> str | None:
        """Normalize an empty terse-parsed device name to ``None``."""

        return device if device else None

    def _nmcli_failed(self, result: NmcliResult) -> dict[str, Any]:
        """Build a structured error from a non-ok :class:`NmcliResult`."""

        return {
            "ok": False,
            "error": "nmcli_failed",
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "timed_out": result.timed_out,
        }

    async def list_vpns(self) -> dict[str, Any]:
        """Return configured VPNs without invoking any subprocess.

        Returns:
            A dictionary with ``ok`` and a sorted list of VPN ids and
            connection names.
        """

        vpns = [
            {"id": profile.id, "connection": profile.connection}
            for profile in (self.config[id_] for id_ in self._sorted_ids())
        ]
        return {"ok": True, "vpns": vpns}

    async def status(self, name: str) -> dict[str, Any]:
        """Report whether ``name``'s connection is active and on which device.

        Args:
            name: Configured VPN id.

        Returns:
            Structured status dictionary, or an unknown-id / nmcli error.
        """

        profile_or_error = self._require_profile(name)
        if isinstance(profile_or_error, dict):
            return profile_or_error
        profile = profile_or_error

        active_result = await self.runner.connection_show_active()
        if not active_result.ok:
            return self._nmcli_failed(active_result)

        for row in parse_active_connections(active_result.stdout):
            if row.name == profile.connection:
                return {
                    "ok": True,
                    "id": name,
                    "connection": profile.connection,
                    "connected": True,
                    "device": self._device_or_none(row.device),
                }

        return {
            "ok": True,
            "id": name,
            "connection": profile.connection,
            "connected": False,
            "device": None,
        }

    async def connect(self, name: str) -> dict[str, Any]:
        """Idempotently activate ``name``'s NetworkManager connection.

        Args:
            name: Configured VPN id.

        Returns:
            Structured result with the verified final state. ``already_connected``
            is ``True`` when no activation command was needed.
        """

        profile_or_error = self._require_profile(name)
        if isinstance(profile_or_error, dict):
            return profile_or_error
        profile = profile_or_error

        status_before = await self.status(name)
        if not status_before["ok"]:
            return status_before

        if status_before["connected"]:
            return {
                "ok": True,
                "id": name,
                "connection": profile.connection,
                "connected": status_before["connected"],
                "device": status_before["device"],
                "already_connected": True,
            }

        up_result = await self.runner.connection_up(profile.connection)
        if not up_result.ok:
            return {
                "ok": False,
                "error": "connect_failed",
                "exit_code": up_result.exit_code,
                "stdout": up_result.stdout,
                "stderr": up_result.stderr,
                "timed_out": up_result.timed_out,
                "detail": f"nmcli connection up failed: {up_result.error}",
            }

        status_after = await self.status(name)
        if not status_after["ok"]:
            return status_after
        if not status_after["connected"]:
            return {
                "ok": False,
                "error": "connect_failed",
                "connected": False,
                "device": None,
                "detail": (
                    "nmcli connection up reported success but the connection "
                    "is not active"
                ),
            }

        return {
            "ok": True,
            "id": name,
            "connection": profile.connection,
            "connected": status_after["connected"],
            "device": status_after["device"],
            "already_connected": False,
        }

    async def disconnect(self, name: str) -> dict[str, Any]:
        """Idempotently deactivate ``name``'s NetworkManager connection.

        If the connection is already inactive, no ``connection down`` command is
        issued and the result reports ``already_disconnected: True``.

        Args:
            name: Configured VPN id.

        Returns:
            Structured result with the verified post-state. ``already_disconnected``
            is ``True`` when no deactivation command was needed.
        """

        profile_or_error = self._require_profile(name)
        if isinstance(profile_or_error, dict):
            return profile_or_error
        profile = profile_or_error

        status_before = await self.status(name)
        if not status_before["ok"]:
            return status_before

        if not status_before["connected"]:
            return {
                "ok": True,
                "id": name,
                "connection": profile.connection,
                "connected": False,
                "device": None,
                "already_disconnected": True,
            }

        down_result = await self.runner.connection_down(profile.connection)
        if not down_result.ok:
            return {
                "ok": False,
                "error": "disconnect_failed",
                "exit_code": down_result.exit_code,
                "stdout": down_result.stdout,
                "stderr": down_result.stderr,
                "timed_out": down_result.timed_out,
                "detail": f"nmcli connection down failed: {down_result.error}",
            }

        status_after = await self.status(name)
        if not status_after["ok"]:
            return status_after

        return {
            "ok": True,
            "id": name,
            "connection": profile.connection,
            "connected": status_after["connected"],
            "device": status_after["device"],
            "already_disconnected": False,
        }

    async def reconnect(self, name: str) -> dict[str, Any]:
        """Health-aware reconnect: diagnose first, rebuild only when needed.

        Because :meth:`disconnect` is idempotent, this works even when the VPN is
        currently disconnected: a tolerant no-op disconnect is followed by
        connect and a final diagnose.

        If either the disconnect or connect stage fails, the failure is returned
        immediately as a structured ``reconnect_failed`` result without running
        the remaining stages.

        Args:
            name: Configured VPN id.

        Returns:
            Structured result. ``reconnected`` is ``False`` when the VPN was
            already usable; ``True`` when disconnect/connect/re-diagnose ran.
        """

        profile_or_error = self._require_profile(name)
        if isinstance(profile_or_error, dict):
            return profile_or_error
        profile = profile_or_error

        diagnose_before = await self.diagnose(name)
        if diagnose_before.get("usable") is True:
            return {
                "ok": True,
                "id": name,
                "connection": profile.connection,
                "reconnected": False,
                "usable": True,
                "diagnose": diagnose_before,
            }

        disconnect_result = await self.disconnect(name)
        if not disconnect_result["ok"]:
            return {
                "ok": False,
                "error": "reconnect_failed",
                "stage": "disconnect",
                "reconnected": False,
                "detail": "disconnect failed during reconnect",
                "disconnect_result": disconnect_result,
            }

        connect_result = await self.connect(name)
        if not connect_result["ok"]:
            return {
                "ok": False,
                "error": "reconnect_failed",
                "stage": "connect",
                "reconnected": False,
                "detail": "connect failed during reconnect",
                "connect_result": connect_result,
            }

        diagnose_after = await self.diagnose(name)
        if not diagnose_after.get("usable"):
            return {
                "ok": False,
                "error": "reconnect_failed",
                "reconnected": True,
                "detail": "reconnect completed but the VPN is not usable",
                "diagnose": diagnose_after,
            }

        return {
            "ok": True,
            "id": name,
            "connection": profile.connection,
            "reconnected": True,
            "usable": True,
            "diagnose": diagnose_after,
        }

    async def diagnose(self, name: str) -> dict[str, Any]:
        """Diagnose local usability of ``name`` without probing remote hosts.

        A timeout or spawn failure while checking whether the profile exists is
        surfaced as a structured error rather than being reported as "profile
        does not exist".

        Args:
            name: Configured VPN id.

        Returns:
            Structured diagnosis dictionary with per-route detail, or a
            structured error dictionary when a subprocess call fails or times
            out.
        """

        profile_or_error = self._require_profile(name)
        if isinstance(profile_or_error, dict):
            return profile_or_error
        profile = profile_or_error

        general = await self.runner.general_status()
        if not general.ok:
            return {
                "ok": False,
                "error": "nm_unresponsive",
                "exit_code": general.exit_code,
                "stdout": general.stdout,
                "stderr": general.stderr,
                "timed_out": general.timed_out,
                "detail": "nmcli general status failed",
            }

        profile_exists_result = await self.runner.connection_show(profile.connection)
        if profile_exists_result.error == "timeout":
            return {
                "ok": False,
                "error": "timeout",
                "exit_code": profile_exists_result.exit_code,
                "stdout": profile_exists_result.stdout,
                "stderr": profile_exists_result.stderr,
                "timed_out": True,
                "detail": "nmcli connection show timed out",
            }
        if profile_exists_result.error == "spawn_failed":
            return {
                "ok": False,
                "error": "nmcli_failed",
                "exit_code": profile_exists_result.exit_code,
                "stdout": profile_exists_result.stdout,
                "stderr": profile_exists_result.stderr,
                "timed_out": False,
                "detail": "nmcli connection show could not be started",
            }
        if not profile_exists_result.ok:
            # Clean non-zero exit means the profile is not known to NM.
            return {
                "ok": True,
                "id": name,
                "connection": profile.connection,
                "connected": False,
                "interface": None,
                "usable": False,
                "checks": {
                    "nm_responsive": True,
                    "profile_exists": False,
                    "connected": False,
                    "interface": None,
                },
                "routes": [],
                "message": "profile does not exist in NetworkManager",
            }

        active_result = await self.runner.connection_show_active()
        if not active_result.ok:
            return self._nmcli_failed(active_result)

        active_device: str | None = None
        connected = False
        for row in parse_active_connections(active_result.stdout):
            if row.name == profile.connection:
                connected = True
                active_device = self._device_or_none(row.device)
                break

        if not connected or active_device is None:
            usable = False
            routes: list[dict[str, Any]] = []
        else:
            routes = []
            all_routes_ok = True
            for network in profile.expected_routes:
                probe = derive_probe(network).probe_address
                route_result = await self.runner.route_get(probe)
                if not route_result.ok:
                    if route_result.timed_out:
                        detail = "route lookup timed out"
                    else:
                        detail = f"route lookup failed: {route_result.error}"
                    routes.append(
                        {
                            "cidr": str(network),
                            "ok": False,
                            "probe": probe,
                            "resolved_interface": None,
                            "detail": detail,
                        }
                    )
                    all_routes_ok = False
                    continue

                route_check = evaluate_route(network, active_device, route_result.stdout)
                routes.append(asdict(route_check))
                if not route_check.ok:
                    all_routes_ok = False

            usable = connected and active_device is not None and all_routes_ok

        return {
            "ok": True,
            "id": name,
            "connection": profile.connection,
            "connected": connected,
            "interface": active_device,
            "usable": usable,
            "checks": {
                "nm_responsive": True,
                "profile_exists": True,
                "connected": connected,
                "interface": active_device,
            },
            "routes": routes,
        }

    async def network_status(self) -> dict[str, Any]:
        """Return an overview of NetworkManager devices.

        Returns:
            Structured device list, or an nmcli error dictionary.
        """

        device_result = await self.runner.device_status()
        if not device_result.ok:
            return self._nmcli_failed(device_result)

        devices = []
        for row in parse_device_status(device_result.stdout):
            devices.append(
                {
                    "device": row.device,
                    "type": row.type_,
                    "state": row.state,
                    "connection": row.connection,
                }
            )
        return {"ok": True, "devices": devices}


