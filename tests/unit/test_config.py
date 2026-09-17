"""Unit tests for ``nmcli_mcp.config``."""

from __future__ import annotations

import ipaddress
import textwrap
from pathlib import Path

import pytest

from nmcli_mcp.config import ConfigError, VpnProfile, config_candidates, load_config


@pytest.fixture
def clear_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset config-related environment variables."""
    monkeypatch.delenv("NMCLI_MCP_CONFIG", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)


@pytest.fixture
def env_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a valid config file and point ``$NMCLI_MCP_CONFIG`` at it."""
    path = tmp_path / "custom-config.toml"
    path.write_text(
        textwrap.dedent(
            """
            [[vpn]]
            id = "env-vpn"
            connection = "Env VPN"
            expected_routes = ["10.10.0.0/16"]
            """
        )
    )
    monkeypatch.setenv("NMCLI_MCP_CONFIG", str(path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return path


@pytest.fixture
def xdg_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a valid config under a temp ``$XDG_CONFIG_HOME``."""
    xdg = tmp_path / "xdg"
    config_dir = xdg / "nmcli-mcp-server"
    config_dir.mkdir(parents=True)
    path = config_dir / "config.toml"
    path.write_text(
        textwrap.dedent(
            """
            [[vpn]]
            id = "xdg-vpn"
            connection = "XDG VPN"
            expected_routes = ["10.11.0.0/16", "fd00:abcd::/64"]
            """
        )
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    return path


@pytest.fixture
def home_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a valid config under a fake ``~/.config``."""
    fake_home = tmp_path / "home"
    config_dir = fake_home / ".config" / "nmcli-mcp-server"
    config_dir.mkdir(parents=True)
    path = config_dir / "config.toml"
    path.write_text(
        textwrap.dedent(
            """
            [[vpn]]
            id = "home-vpn"
            connection = "Home VPN"
            """
        )
    )
    monkeypatch.setenv("HOME", str(fake_home))
    return path


def _write_config(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content))


def test_xdg_default_path_resolution(
    clear_config_env: None, xdg_config: Path
) -> None:
    """Loading without env override uses the XDG default path."""
    # clear_config_env must be instantiated BEFORE xdg_config: fixtures share
    # one monkeypatch per test, and a later delenv would undo the earlier
    # setenv.
    config = load_config()
    assert "xdg-vpn" in config
    assert config["xdg-vpn"].connection == "XDG VPN"
    assert config["xdg-vpn"].expected_routes == (
        ipaddress.ip_network("10.11.0.0/16"),
        ipaddress.ip_network("fd00:abcd::/64"),
    )


def test_env_override_wins(env_config: Path, xdg_config: Path) -> None:
    """``$NMCLI_MCP_CONFIG`` takes precedence over the XDG path."""
    config = load_config()
    assert "env-vpn" in config
    assert "xdg-vpn" not in config
    assert config["env-vpn"].connection == "Env VPN"


def test_home_config_fallback(home_config: Path, clear_config_env: None) -> None:
    """Loading falls back to ``~/.config`` when env and XDG are unset."""
    config = load_config()
    assert "home-vpn" in config
    assert config["home-vpn"].connection == "Home VPN"


def test_missing_config_lists_all_tried_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing config reports every candidate path that was tried."""
    monkeypatch.setenv("NMCLI_MCP_CONFIG", str(tmp_path / "env-config.toml"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    with pytest.raises(ConfigError) as exc_info:
        load_config()

    message = str(exc_info.value)
    assert "config file not found; tried:" in message
    assert "NMCLI_MCP_CONFIG" in message
    assert "env-config.toml" in message
    assert "xdg/nmcli-mcp-server/config.toml" in message
    assert "home/.config/nmcli-mcp-server/config.toml" in message


def test_invalid_id_fails_startup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An id that fails the allowlist regex is rejected and identified."""
    path = tmp_path / "bad-id.toml"
    _write_config(
        path,
            """
            [[vpn]]
            id = "Intetics"
            connection = "Intetics VPN"
            """,
    )
    monkeypatch.setenv("NMCLI_MCP_CONFIG", str(path))

    with pytest.raises(ConfigError) as exc_info:
        load_config()

    message = str(exc_info.value)
    assert "index 0" in message
    assert "Intetics" in message
    assert "^[a-z][a-z0-9-]*$" in message


def test_ambiguous_cidr_fails_startup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A route with host bits set is rejected and identifies the entry/route."""
    path = tmp_path / "ambiguous.toml"
    _write_config(
        path,
            """
            [[vpn]]
            id = "testvpn"
            connection = "Test VPN"
            expected_routes = ["10.8.0.5/24"]
            """,
    )
    monkeypatch.setenv("NMCLI_MCP_CONFIG", str(path))

    with pytest.raises(ConfigError) as exc_info:
        load_config()

    message = str(exc_info.value)
    assert "testvpn" in message
    assert "10.8.0.5/24" in message


def test_duplicate_id_fails_startup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A duplicate id is rejected and named."""
    path = tmp_path / "dup.toml"
    _write_config(
        path,
            """
            [[vpn]]
            id = "same"
            connection = "First"

            [[vpn]]
            id = "same"
            connection = "Second"
            """,
    )
    monkeypatch.setenv("NMCLI_MCP_CONFIG", str(path))

    with pytest.raises(ConfigError) as exc_info:
        load_config()

    assert "same" in str(exc_info.value)


def test_empty_connection_fails_startup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty connection name is rejected."""
    path = tmp_path / "empty-conn.toml"
    _write_config(
        path,
            """
            [[vpn]]
            id = "novpn"
            connection = ""
            """,
    )
    monkeypatch.setenv("NMCLI_MCP_CONFIG", str(path))

    with pytest.raises(ConfigError) as exc_info:
        load_config()

    message = str(exc_info.value)
    assert "novpn" in message
    assert "empty 'connection'" in message


def test_unknown_key_in_vpn_table_fails_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unknown key inside ``[[vpn]]`` is rejected and named."""
    path = tmp_path / "unknown-key.toml"
    _write_config(
        path,
            """
            [[vpn]]
            id = "known"
            connection = "Known"
            expectd_routes = ["10.0.0.0/8"]
            """,
    )
    monkeypatch.setenv("NMCLI_MCP_CONFIG", str(path))

    with pytest.raises(ConfigError) as exc_info:
        load_config()

    message = str(exc_info.value)
    assert "expectd_routes" in message
    assert "known" in str(exc_info.value) or "index 0" in message


def test_unparseable_toml_fails_startup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unparseable TOML content raises :class:`ConfigError`."""
    path = tmp_path / "bad.toml"
    path.write_text(
        textwrap.dedent(
            """
            [vpn
            id = "broken"
            """
        )
    )
    monkeypatch.setenv("NMCLI_MCP_CONFIG", str(path))

    with pytest.raises(ConfigError) as exc_info:
        load_config()

    assert str(path) in str(exc_info.value)


def test_valid_multi_vpn_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A config with multiple VPNs and mixed IPv4/IPv6 routes loads correctly."""
    path = tmp_path / "multi.toml"
    _write_config(
        path,
            """
            [[vpn]]
            id = "intetics"
            connection = "Intetics VPN"
            expected_routes = ["10.13.0.0/16", "fd00:abcd::/64"]

            [[vpn]]
            id = "testvpn"
            connection = "Test VPN"
            expected_routes = ["192.168.100.0/24"]

            [[vpn]]
            id = "plain"
            connection = "Plain VPN"
            """,
    )
    monkeypatch.setenv("NMCLI_MCP_CONFIG", str(path))

    config = load_config()
    assert len(config) == 3
    assert config["intetics"] == VpnProfile(
        id="intetics",
        connection="Intetics VPN",
        expected_routes=(
            ipaddress.ip_network("10.13.0.0/16"),
            ipaddress.ip_network("fd00:abcd::/64"),
        ),
    )
    assert config["testvpn"].expected_routes == (
        ipaddress.ip_network("192.168.100.0/24"),
    )
    assert config["plain"].expected_routes == ()


def test_no_vpn_key_is_empty_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A TOML file without a ``vpn`` key is valid and yields an empty mapping."""
    path = tmp_path / "empty.toml"
    path.write_text("\n")
    monkeypatch.setenv("NMCLI_MCP_CONFIG", str(path))

    config = load_config()
    assert config == {}


def test_empty_vpn_list_is_valid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit empty ``vpn`` list is valid and yields an empty mapping."""
    path = tmp_path / "empty-list.toml"
    path.write_text("vpn = []\n")
    monkeypatch.setenv("NMCLI_MCP_CONFIG", str(path))

    config = load_config()
    assert config == {}


def test_unreadable_config_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file that exists but cannot be read raises :class:`ConfigError`."""
    path = tmp_path / "secret.toml"
    _write_config(path, "[[vpn]]\nid = \"x\"\nconnection = \"X\"\n")
    monkeypatch.setenv("NMCLI_MCP_CONFIG", str(path))
    path.chmod(0o000)

    try:
        with pytest.raises(ConfigError) as exc_info:
            load_config()
        assert str(path) in str(exc_info.value)
        assert "unreadable" in str(exc_info.value)
    finally:
        path.chmod(0o644)


def test_config_candidates_returns_ordered_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """``config_candidates`` returns resolution-ordered paths."""
    monkeypatch.setenv("NMCLI_MCP_CONFIG", "/a/b.toml")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/xdg")
    monkeypatch.setenv("HOME", "/home")

    candidates = config_candidates()
    assert candidates == [
        Path("/a/b.toml"),
        Path("/xdg/nmcli-mcp-server/config.toml"),
        Path("/home/.config/nmcli-mcp-server/config.toml"),
    ]
