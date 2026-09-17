"""Unit tests for ``nmcli_mcp.nmcli``.

All subprocess calls are mocked; these tests never invoke the real ``nmcli``
or ``ip`` binaries, so they run on macOS and in CI without NetworkManager.
"""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

import pytest

from nmcli_mcp.nmcli import (
    IP_BIN,
    IP_TIMEOUT_S,
    NMCLI_BIN,
    NMCLI_TIMEOUT_S,
    ActiveConnection,
    DeviceRow,
    NmcliRunner,
    parse_active_connections,
    parse_device_status,
)


@dataclass
class FakeProc:
    """Minimal stand-in for ``asyncio.subprocess.Process``."""

    returncode: int | None
    stdout: bytes
    stderr: bytes
    _communicate_delay: float | None = None
    killed: bool = False
    waited: bool = False
    pid: int | None = None

    async def communicate(self) -> tuple[bytes, bytes]:
        if self._communicate_delay is not None:
            await asyncio.sleep(self._communicate_delay)
        return (self.stdout, self.stderr)

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int | None:
        self.waited = True
        return self.returncode


FakeExec = Callable[..., Coroutine[Any, Any, FakeProc]]


def _make_exec(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
    delay: float | None = None,
) -> FakeExec:
    async def _exec(*args: Any, **kwargs: Any) -> FakeProc:
        assert kwargs.get("stdout") is asyncio.subprocess.PIPE
        assert kwargs.get("stderr") is asyncio.subprocess.PIPE
        assert kwargs.get("start_new_session") is True
        return FakeProc(
            returncode=returncode,
            stdout=stdout.encode("utf-8"),
            stderr=stderr.encode("utf-8"),
            _communicate_delay=delay,
        )

    return _exec


@pytest.mark.asyncio
async def test_general_status_success_returns_ok_with_stdout(monkeypatch: Any) -> None:
    expected_stdout = "connected\n"
    monkeypatch.setattr(
        "nmcli_mcp.nmcli.asyncio.create_subprocess_exec",
        _make_exec(returncode=0, stdout=expected_stdout, stderr=""),
    )

    runner = NmcliRunner()
    result = await runner.general_status()

    assert result.ok is True
    assert result.exit_code == 0
    assert result.stdout == expected_stdout
    assert result.error is None
    assert result.argv == (NMCLI_BIN, "general", "status")


@pytest.mark.asyncio
async def test_nonzero_exit_returns_structured_error(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "nmcli_mcp.nmcli.asyncio.create_subprocess_exec",
        _make_exec(returncode=1, stdout="", stderr="Error: unknown connection."),
    )

    runner = NmcliRunner()
    result = await runner.connection_show("missing")

    assert result.ok is False
    assert result.exit_code == 1
    assert result.error == "exit"
    assert result.stderr == "Error: unknown connection."


@pytest.mark.asyncio
async def test_timeout_kills_process_and_returns_timeout_result(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "nmcli_mcp.nmcli.asyncio.create_subprocess_exec",
        _make_exec(delay=10.0),
    )

    runner = NmcliRunner(nmcli_timeout_s=0.01)
    result = await runner.connection_up("vpn")

    assert result.ok is False
    assert result.exit_code is None
    assert result.error == "timeout"
    assert result.timed_out is True


@pytest.mark.asyncio
async def test_timeout_invokes_kill_and_wait(monkeypatch: Any) -> None:
    proc = FakeProc(returncode=None, stdout=b"", stderr=b"", _communicate_delay=10.0)

    async def _exec(*args: Any, **kwargs: Any) -> FakeProc:
        assert kwargs.get("stdout") is asyncio.subprocess.PIPE
        assert kwargs.get("stderr") is asyncio.subprocess.PIPE
        assert kwargs.get("start_new_session") is True
        return proc

    monkeypatch.setattr("nmcli_mcp.nmcli.asyncio.create_subprocess_exec", _exec)

    runner = NmcliRunner(nmcli_timeout_s=0.01)
    await runner.connection_up("vpn")

    assert proc.killed is True
    assert proc.waited is True


@pytest.mark.asyncio
async def test_timeout_kills_whole_process_group(monkeypatch: Any) -> None:
    """On timeout the group kill must fire when the fake exposes a pid."""

    killpg_calls: list[tuple[int, int]] = []

    def _fake_killpg(pgid: int, sig: int) -> None:
        killpg_calls.append((pgid, sig))

    monkeypatch.setattr("nmcli_mcp.nmcli.os.killpg", _fake_killpg)

    proc = FakeProc(
        returncode=None,
        stdout=b"",
        stderr=b"",
        _communicate_delay=10.0,
        pid=4242,
    )

    async def _exec(*args: Any, **kwargs: Any) -> FakeProc:
        assert kwargs.get("stdout") is asyncio.subprocess.PIPE
        assert kwargs.get("stderr") is asyncio.subprocess.PIPE
        assert kwargs.get("start_new_session") is True
        return proc

    monkeypatch.setattr("nmcli_mcp.nmcli.asyncio.create_subprocess_exec", _exec)

    runner = NmcliRunner(nmcli_timeout_s=0.01)
    result = await runner.connection_up("vpn")

    assert proc.killed is True
    assert proc.waited is True
    assert result.ok is False
    assert result.exit_code is None
    assert result.error == "timeout"
    assert result.timed_out is True
    assert len(killpg_calls) == 1
    assert killpg_calls[0] == (4242, signal.SIGKILL)


@pytest.mark.asyncio
async def test_spawn_filenotfound_returns_structured_error_for_nmcli(monkeypatch: Any) -> None:
    async def _exec(*args: Any, **kwargs: Any) -> FakeProc:
        raise FileNotFoundError("nmcli")

    monkeypatch.setattr("nmcli_mcp.nmcli.asyncio.create_subprocess_exec", _exec)

    runner = NmcliRunner()
    result = await runner.general_status()

    assert result.ok is False
    assert result.exit_code is None
    assert result.error == "spawn_failed"
    assert "nmcli" in result.stderr
    assert result.argv == (NMCLI_BIN, "general", "status")


@pytest.mark.asyncio
async def test_spawn_filenotfound_returns_structured_error_for_ip_route_get(monkeypatch: Any) -> None:
    async def _exec(*args: Any, **kwargs: Any) -> FakeProc:
        raise FileNotFoundError("ip")

    monkeypatch.setattr("nmcli_mcp.nmcli.asyncio.create_subprocess_exec", _exec)

    runner = NmcliRunner()
    result = await runner.route_get("10.8.0.1")

    assert result.ok is False
    assert result.exit_code is None
    assert result.error == "spawn_failed"
    assert "ip" in result.stderr
    assert result.argv == (IP_BIN, "route", "get", "10.8.0.1")


@pytest.mark.asyncio
async def test_spawn_permission_error_returns_structured_error(monkeypatch: Any) -> None:
    async def _exec(*args: Any, **kwargs: Any) -> FakeProc:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("nmcli_mcp.nmcli.asyncio.create_subprocess_exec", _exec)

    runner = NmcliRunner()
    result = await runner.connection_show("vpn")

    assert result.ok is False
    assert result.exit_code is None
    assert result.error == "spawn_failed"
    assert "Permission denied" in result.stderr


@pytest.mark.parametrize(
    ("method_name", "input_value", "expected_argv"),
    [
        ("connection_show", "x; rm -rf /", (NMCLI_BIN, "connection", "show", "x; rm -rf /")),
        ("connection_up", "$(reboot)", (NMCLI_BIN, "connection", "up", "$(reboot)")),
        ("connection_down", "--flag", (NMCLI_BIN, "connection", "down", "--flag")),
        ("route_get", "; cat /etc/passwd", (IP_BIN, "route", "get", "; cat /etc/passwd")),
    ],
)
@pytest.mark.asyncio
async def test_adversarial_input_appears_only_as_literal_final_argument(
    monkeypatch: Any,
    method_name: str,
    input_value: str,
    expected_argv: tuple[str, ...],
) -> None:
    captured_argv: list[str] | None = None

    async def _exec(*args: Any, **kwargs: Any) -> FakeProc:
        assert kwargs.get("stdout") is asyncio.subprocess.PIPE
        assert kwargs.get("stderr") is asyncio.subprocess.PIPE
        assert kwargs.get("start_new_session") is True
        nonlocal captured_argv
        captured_argv = list(args)
        return FakeProc(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr("nmcli_mcp.nmcli.asyncio.create_subprocess_exec", _exec)

    runner = NmcliRunner()
    method = getattr(runner, method_name)
    result = await method(input_value)

    assert result.ok is True
    assert captured_argv is not None
    assert tuple(captured_argv) == expected_argv


@pytest.mark.asyncio
async def test_all_runner_methods_emit_exact_fixed_argv(monkeypatch: Any) -> None:
    calls: list[tuple[str, ...]] = []

    async def _exec(*args: Any, **kwargs: Any) -> FakeProc:
        assert kwargs.get("stdout") is asyncio.subprocess.PIPE
        assert kwargs.get("stderr") is asyncio.subprocess.PIPE
        assert kwargs.get("start_new_session") is True
        calls.append(tuple(args))
        return FakeProc(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr("nmcli_mcp.nmcli.asyncio.create_subprocess_exec", _exec)

    runner = NmcliRunner()
    await runner.general_status()
    await runner.connection_show_active()
    await runner.connection_show("Intetics VPN")
    await runner.connection_up("Intetics VPN")
    await runner.connection_down("Intetics VPN")
    await runner.device_status()
    await runner.route_get("10.8.0.1")

    assert calls == [
        (NMCLI_BIN, "general", "status"),
        (NMCLI_BIN, "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active"),
        (NMCLI_BIN, "connection", "show", "Intetics VPN"),
        (NMCLI_BIN, "connection", "up", "Intetics VPN"),
        (NMCLI_BIN, "connection", "down", "Intetics VPN"),
        (NMCLI_BIN, "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"),
        (IP_BIN, "route", "get", "10.8.0.1"),
    ]


@pytest.mark.asyncio
async def test_custom_bin_names_are_used_in_argv(monkeypatch: Any) -> None:
    captured: list[str] | None = None

    async def _exec(*args: Any, **kwargs: Any) -> FakeProc:
        assert kwargs.get("stdout") is asyncio.subprocess.PIPE
        assert kwargs.get("stderr") is asyncio.subprocess.PIPE
        assert kwargs.get("start_new_session") is True
        nonlocal captured
        captured = list(args)
        return FakeProc(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr("nmcli_mcp.nmcli.asyncio.create_subprocess_exec", _exec)

    runner = NmcliRunner(nmcli_bin="/opt/nmcli", ip_bin="/opt/ip")
    await runner.general_status()

    assert captured is not None
    assert captured[0] == "/opt/nmcli"

    await runner.route_get("1.1.1.1")
    assert captured is not None
    assert captured[0] == "/opt/ip"


def test_parse_active_connections_with_colon_in_name() -> None:
    text = "Intetics\\: VPN:vpn:wg0\n"
    rows = parse_active_connections(text)

    assert rows == [ActiveConnection(name="Intetics: VPN", type_="vpn", device="wg0")]


def test_parse_active_connections_with_empty_device() -> None:
    text = "name:vpn:\n"
    rows = parse_active_connections(text)

    assert rows == [ActiveConnection(name="name", type_="vpn", device="")]


def test_parse_active_connections_multiple_rows() -> None:
    text = "Intetics\\: VPN:vpn:wg0\nOffice\\ VPN:wired:eth0\n"
    rows = parse_active_connections(text)

    assert rows == [
        ActiveConnection(name="Intetics: VPN", type_="vpn", device="wg0"),
        ActiveConnection(name="Office\\ VPN", type_="wired", device="eth0"),
    ]


def test_parse_active_connections_backslash_in_name() -> None:
    text = "Office\\\\ VPN:wired:eth0\n"
    rows = parse_active_connections(text)

    assert rows == [ActiveConnection(name="Office\\ VPN", type_="wired", device="eth0")]


def test_parse_active_connections_backslash_then_separator_at_end_of_name() -> None:
    # Raw nmcli output: "a\" followed by escaped backslash, then real separator.
    text = "a\\\\:vpn:wg0\n"
    rows = parse_active_connections(text)

    assert rows == [ActiveConnection(name="a\\", type_="vpn", device="wg0")]


def test_parse_active_connections_empty_input() -> None:
    assert parse_active_connections("") == []
    assert parse_active_connections("\n\n   \n") == []


def test_parse_active_connections_ignores_extra_trailing_fields() -> None:
    text = "name:vpn:wg0:extra\n"
    rows = parse_active_connections(text)

    assert rows == [ActiveConnection(name="name", type_="vpn", device="wg0")]


def test_parse_active_connections_tolerates_missing_trailing_fields() -> None:
    text = "name:vpn\n"
    rows = parse_active_connections(text)

    assert rows == [ActiveConnection(name="name", type_="vpn", device="")]


def test_parse_device_status_with_colon_in_connection() -> None:
    text = "wg0:vpn:connected:Corp\\: VPN\n"
    rows = parse_device_status(text)

    assert rows == [
        DeviceRow(device="wg0", type_="vpn", state="connected", connection="Corp: VPN")
    ]


def test_parse_device_status_backslash_in_connection_value() -> None:
    # Escaped backslash mid-value, followed later by a real separator.
    text = "wg0:vpn:connected:Domain\\ Office\\:corp\n"
    rows = parse_device_status(text)

    assert rows == [
        DeviceRow(device="wg0", type_="vpn", state="connected", connection="Domain\\ Office:corp")
    ]


def test_parse_backslash_before_ordinary_char_is_preserved_literally() -> None:
    text = "a\\b:vpn:wg0\n"
    rows = parse_active_connections(text)

    assert rows == [ActiveConnection(name="a\\b", type_="vpn", device="wg0")]


def test_parse_device_status_empty_connection_as_two_quotes() -> None:
    text = 'lo:loopback:unmanaged:""\n'
    rows = parse_device_status(text)

    assert rows == [
        DeviceRow(device="lo", type_="loopback", state="unmanaged", connection="")
    ]


def test_parse_device_status_unbound_device_with_trailing_empty_field() -> None:
    text = "lo:loopback:unmanaged:\n"
    rows = parse_device_status(text)

    assert rows == [
        DeviceRow(device="lo", type_="loopback", state="unmanaged", connection="")
    ]


def test_parse_device_status_multiple_rows_and_empty_lines() -> None:
    text = "\neth0:wired:connected:Wired connection\n\nwg0:vpn:connected:Test VPN\n"
    rows = parse_device_status(text)

    assert rows == [
        DeviceRow(device="eth0", type_="wired", state="connected", connection="Wired connection"),
        DeviceRow(device="wg0", type_="vpn", state="connected", connection="Test VPN"),
    ]


@pytest.mark.asyncio
async def test_route_get_success_shape_and_uses_five_second_timeout(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    async def _exec(*args: Any, **kwargs: Any) -> FakeProc:
        assert kwargs.get("stdout") is asyncio.subprocess.PIPE
        assert kwargs.get("stderr") is asyncio.subprocess.PIPE
        assert kwargs.get("start_new_session") is True
        captured["argv"] = tuple(args)
        return FakeProc(returncode=0, stdout=b"10.8.0.1 dev wg0\n", stderr=b"")

    monkeypatch.setattr("nmcli_mcp.nmcli.asyncio.create_subprocess_exec", _exec)

    runner = NmcliRunner()
    result = await runner.route_get("10.8.0.1")

    assert result.ok is True
    assert result.stdout == "10.8.0.1 dev wg0\n"
    assert captured["argv"] == (IP_BIN, "route", "get", "10.8.0.1")


@pytest.mark.asyncio
async def test_nmcli_timeout_value_is_thirty_seconds(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    async def _exec(*args: Any, **kwargs: Any) -> FakeProc:
        assert kwargs.get("stdout") is asyncio.subprocess.PIPE
        assert kwargs.get("stderr") is asyncio.subprocess.PIPE
        assert kwargs.get("start_new_session") is True
        return FakeProc(returncode=0, stdout=b"", stderr=b"")

    async def _wait_for(coro: Coroutine[Any, Any, Any], timeout: float) -> Any:
        captured["timeout"] = timeout
        return await coro

    monkeypatch.setattr("nmcli_mcp.nmcli.asyncio.create_subprocess_exec", _exec)
    monkeypatch.setattr("nmcli_mcp.nmcli.asyncio.wait_for", _wait_for)

    runner = NmcliRunner()
    await runner.general_status()

    assert captured["timeout"] == NMCLI_TIMEOUT_S


@pytest.mark.asyncio
async def test_ip_timeout_value_is_five_seconds(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    async def _exec(*args: Any, **kwargs: Any) -> FakeProc:
        assert kwargs.get("stdout") is asyncio.subprocess.PIPE
        assert kwargs.get("stderr") is asyncio.subprocess.PIPE
        assert kwargs.get("start_new_session") is True
        return FakeProc(returncode=0, stdout=b"", stderr=b"")

    async def _wait_for(coro: Coroutine[Any, Any, Any], timeout: float) -> Any:
        captured["timeout"] = timeout
        return await coro

    monkeypatch.setattr("nmcli_mcp.nmcli.asyncio.create_subprocess_exec", _exec)
    monkeypatch.setattr("nmcli_mcp.nmcli.asyncio.wait_for", _wait_for)

    runner = NmcliRunner()
    await runner.route_get("10.8.0.1")

    assert captured["timeout"] == IP_TIMEOUT_S


@pytest.mark.asyncio
async def test_decoding_with_invalid_utf8_bytes_replaces_them(monkeypatch: Any) -> None:
    async def _exec(*args: Any, **kwargs: Any) -> FakeProc:
        assert kwargs.get("stdout") is asyncio.subprocess.PIPE
        assert kwargs.get("stderr") is asyncio.subprocess.PIPE
        assert kwargs.get("start_new_session") is True
        return FakeProc(
            returncode=0,
            stdout=b"\xff\xfe ok",
            stderr=b"",
        )

    monkeypatch.setattr(
        "nmcli_mcp.nmcli.asyncio.create_subprocess_exec",
        _exec,
    )

    runner = NmcliRunner()
    result = await runner.general_status()

    assert result.ok is True
    assert "\ufffd\ufffd ok" in result.stdout
