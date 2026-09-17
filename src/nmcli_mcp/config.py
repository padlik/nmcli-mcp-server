"""VPN profile configuration loading and validation.

Config path resolution (first match wins):
1. ``$NMCLI_MCP_CONFIG`` (if set and non-empty)
2. ``$XDG_CONFIG_HOME/nmcli-mcp-server/config.toml``
3. ``~/.config/nmcli-mcp-server/config.toml``

The TOML file contains zero or more ``[[vpn]]`` tables:

.. code-block:: toml

    [[vpn]]
    id = "intetics"
    connection = "Intetics VPN"
    expected_routes = ["10.13.0.0/16", "fd00:abcd::/64"]
"""

from __future__ import annotations

import ipaddress
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:  # pragma: no cover
    import tomli as tomllib


class ConfigError(Exception):
    """Raised when config resolution, parsing, or validation fails."""


@dataclass(frozen=True)
class VpnProfile:
    """A validated VPN profile from TOML config.

    Attributes:
        id: Public identifier used by MCP tools. Lowercase, starts with a
            letter, may contain letters, digits, and hyphens.
        connection: Exact NetworkManager connection name.
        expected_routes: Parsed expected CIDR routes as network objects.
    """

    id: str
    connection: str
    expected_routes: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]


VpnConfig = Mapping[str, VpnProfile]

_ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_VALID_VPN_KEYS = {"id", "connection", "expected_routes"}


def config_candidates() -> list[Path]:
    """Return ordered candidate config paths without checking existence.

    Returns:
        A list of :class:`pathlib.Path` objects in resolution order.
    """
    candidates: list[Path] = []

    env_path = os.environ.get("NMCLI_MCP_CONFIG")
    if env_path:
        candidates.append(Path(env_path))

    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        candidates.append(Path(xdg_config_home) / "nmcli-mcp-server" / "config.toml")

    candidates.append(Path.home() / ".config" / "nmcli-mcp-server" / "config.toml")
    return candidates


def _format_tried_paths(candidates: list[Path]) -> str:
    parts: list[str] = []
    for path in candidates:
        env_name = _env_name_for_path(path)
        if env_name:
            parts.append(f"{env_name}={path}")
        else:
            parts.append(str(path))
    return ", ".join(parts)


def _env_name_for_path(path: Path) -> str | None:
    """Return the environment variable name for a candidate path, if any."""
    env_path = os.environ.get("NMCLI_MCP_CONFIG")
    if env_path and Path(env_path) == path:
        return "NMCLI_MCP_CONFIG"
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home and Path(xdg_config_home) / "nmcli-mcp-server" / "config.toml" == path:
        return "XDG_CONFIG_HOME"
    return None


def _resolve_path(candidates: list[Path]) -> Path:
    """Pick the first existing candidate path.

    Args:
        candidates: Ordered list of config file paths to try.

    Returns:
        Path to an existing config file.

    Raises:
        ConfigError: If none of the candidates exist.
    """
    for path in candidates:
        if path.exists():
            return path
    raise ConfigError(
        "config file not found; tried: " + _format_tried_paths(candidates)
    )


def _load_toml(path: Path) -> dict[str, Any]:
    """Load and parse a TOML config file.

    Args:
        path: Existing config file path.

    Returns:
        Parsed TOML document as a dictionary.

    Raises:
        ConfigError: If the file cannot be read or parsed.
    """
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except OSError as exc:
        raise ConfigError(f"config file unreadable at {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"config file unparseable at {path}: {exc}") from exc


def _validate_root(root: Any, path: Path) -> list[dict[str, Any]]:
    """Validate the top-level TOML structure.

    Args:
        root: Parsed TOML root object.
        path: Config file path for error messages.

    Returns:
        List of raw ``[[vpn]]`` table dictionaries (possibly empty).

    Raises:
        ConfigError: If the root is not a table or ``vpn`` has the wrong type.
    """
    if not isinstance(root, dict):
        raise ConfigError(
            f"config root must be a table in {path}, got {type(root).__name__}"
        )

    raw_vpn = root.get("vpn")
    if raw_vpn is None:
        return []
    if not isinstance(raw_vpn, list):
        raise ConfigError(
            f"config key 'vpn' must be a list of tables in {path}, "
            f"got {type(raw_vpn).__name__}"
        )
    return raw_vpn


def _validate_entry(raw: Any, index: int) -> dict[str, Any]:
    """Validate that a single ``[[vpn]]`` entry is a table with known keys.

    Args:
        raw: Raw TOML value for the entry.
        index: Zero-based position in the ``vpn`` list.

    Returns:
        The casted table dictionary.

    Raises:
        ConfigError: If the entry is malformed or contains unknown keys.
    """
    if not isinstance(raw, dict):
        raise ConfigError(
            f"vpn entry at index {index} must be a table, got {type(raw).__name__}"
        )
    unknown = sorted(set(raw.keys()) - _VALID_VPN_KEYS)
    if unknown:
        raise ConfigError(
            f"vpn entry at index {index} contains unknown key(s): "
            + ", ".join(unknown)
        )
    return raw


def _validate_id(value: Any, index: int) -> str:
    """Validate the ``id`` field of a ``[[vpn]]`` entry.

    Args:
        value: Raw TOML value for ``id``.
        index: Zero-based position in the ``vpn`` list.

    Returns:
        Validated id string.

    Raises:
        ConfigError: If the id is missing, wrong type, or fails the regex.
    """
    if value is None:
        raise ConfigError(f"vpn entry at index {index} is missing required 'id'")
    if not isinstance(value, str):
        raise ConfigError(
            f"vpn entry at index {index} has invalid 'id' type "
            f"{type(value).__name__}, expected string"
        )
    if not value:
        raise ConfigError(f"vpn entry at index {index} has empty 'id'")
    if not _ID_RE.match(value):
        raise ConfigError(
            f"vpn entry at index {index} has invalid id {value!r}: "
            "must match ^[a-z][a-z0-9-]*$"
        )
    return value


def _validate_connection(value: Any, vpn_id: str, index: int) -> str:
    """Validate the ``connection`` field of a ``[[vpn]]`` entry.

    Args:
        value: Raw TOML value for ``connection``.
        vpn_id: Validated id of the entry (used in messages).
        index: Zero-based position in the ``vpn`` list.

    Returns:
        Validated connection string.

    Raises:
        ConfigError: If the connection is missing, wrong type, or empty.
    """
    if value is None:
        raise ConfigError(
            f"vpn entry at index {index} (id={vpn_id}) is missing required 'connection'"
        )
    if not isinstance(value, str):
        raise ConfigError(
            f"vpn entry at index {index} (id={vpn_id}) has invalid 'connection' type "
            f"{type(value).__name__}, expected string"
        )
    if not value.strip():
        raise ConfigError(
            f"vpn entry at index {index} (id={vpn_id}) has empty 'connection'"
        )
    return value


def _validate_routes(
    value: Any, vpn_id: str, index: int
) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """Validate and parse ``expected_routes`` for a ``[[vpn]]`` entry.

    Args:
        value: Raw TOML value for ``expected_routes``.
        vpn_id: Validated id of the entry (used in messages).
        index: Zero-based position in the ``vpn`` list.

    Returns:
        Tuple of parsed :mod:`ipaddress` network objects.

    Raises:
        ConfigError: If routes is the wrong type, contains non-strings, or a
            CIDR is invalid (including host-bits-set under strict parsing).
    """
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ConfigError(
            f"vpn entry at index {index} (id={vpn_id}) has invalid "
            f"'expected_routes' type {type(value).__name__}, expected list of strings"
        )

    parsed: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for route_str in value:
        if not isinstance(route_str, str):
            raise ConfigError(
                f"vpn entry at index {index} (id={vpn_id}) has non-string "
                f"route {route_str!r} in 'expected_routes'"
            )
        try:
            parsed.append(ipaddress.ip_network(route_str, strict=True))
        except ValueError as exc:
            raise ConfigError(
                f"vpn entry at index {index} (id={vpn_id}) has invalid route "
                f"{route_str!r}: {exc}"
            ) from exc
    return tuple(parsed)


def _parse_vpn_tables(raw_tables: list[dict[str, Any]]) -> VpnConfig:
    """Parse and validate a list of raw ``[[vpn]]`` tables.

    Args:
        raw_tables: Raw dictionaries from TOML parsing.

    Returns:
        Mapping from validated VPN id to :class:`VpnProfile`.

    Raises:
        ConfigError: If any entry is invalid or ids are duplicated.
    """
    profiles: dict[str, VpnProfile] = {}

    for index, raw in enumerate(raw_tables):
        table = _validate_entry(raw, index)
        vpn_id = _validate_id(table.get("id"), index)
        connection = _validate_connection(table.get("connection"), vpn_id, index)
        expected_routes = _validate_routes(
            table.get("expected_routes"), vpn_id, index
        )

        if vpn_id in profiles:
            raise ConfigError(f"duplicate vpn id {vpn_id!r}")

        profiles[vpn_id] = VpnProfile(
            id=vpn_id,
            connection=connection,
            expected_routes=expected_routes,
        )

    return profiles


def load_config() -> VpnConfig:
    """Resolve, load, and validate the VPN configuration once.

    Returns:
        Mapping from VPN id to validated :class:`VpnProfile`.

    Raises:
        ConfigError: On resolution, parsing, or validation failure.
    """
    candidates = config_candidates()
    path = _resolve_path(candidates)
    root = _load_toml(path)
    raw_tables = _validate_root(root, path)
    return _parse_vpn_tables(raw_tables)
