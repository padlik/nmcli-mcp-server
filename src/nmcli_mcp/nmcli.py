"""Sole subprocess adapter for NetworkManager (`nmcli`) and routing (`ip`).

This module is the only place in the project where `nmcli` or `ip` are
executed. It exposes a thin, mockable `NmcliRunner` class and synchronous
terse-output parsers for the `nmcli -t -f ...` shapes consumed by the rest
of the application.

Design guarantees:

* argv is assembled only from module-level constants and the single
  caller-provided `connection` or `probe` value.
* Subprocesses are spawned with `asyncio.create_subprocess_exec` and no
  shell is ever involved.
* Each subprocess starts a new session (its own process group). On timeout,
  the whole process group is killed, not just the direct child, so
  long-lived grandchildren are not left behind.
* Timeouts produce a structured `NmcliResult` with ``error == "timeout"``,
  never a raised exception or hang.
* Non-zero exits produce a structured `NmcliResult` with
  ``error == "exit"``, preserving stdout/stderr.
* Spawn failures (``OSError`` such as ``FileNotFoundError`` /
  ``PermissionError``) produce a structured `NmcliResult` with
  ``error == "spawn_failed"`` and a diagnostic stderr, never a raised
  exception.
* Decoding uses ``errors="replace"`` so unexpected bytes never crash the
  server.
* No logging or printing happens here; diagnostics are returned in the
  result fields.
"""

from __future__ import annotations

import asyncio
import os
import signal
from dataclasses import dataclass
from typing import Final

NMCLI_BIN: Final = "nmcli"
IP_BIN: Final = "ip"
NMCLI_TIMEOUT_S: Final = 30.0
IP_TIMEOUT_S: Final = 5.0


__all__ = [
    "IP_BIN",
    "IP_TIMEOUT_S",
    "NMCLI_BIN",
    "NMCLI_TIMEOUT_S",
    "ActiveConnection",
    "DeviceRow",
    "NmcliResult",
    "NmcliRunner",
    "parse_active_connections",
    "parse_device_status",
]


@dataclass
class NmcliResult:
    """Structured result of an nmcli/ip subprocess run.

    Attributes:
        ok: True if the process exited with return code 0.
        exit_code: Process return code, or ``None`` for timeout.
        stdout: Decoded standard output.
        stderr: Decoded standard error.
        error: ``"timeout"`` on timeout, ``"exit"`` on non-zero exit,
            ``"spawn_failed"`` when the process cannot be spawned,
            otherwise ``None``.
        argv: The exact argv tuple passed to the subprocess, for diagnostics.
    """

    ok: bool
    exit_code: int | None
    stdout: str
    stderr: str
    error: str | None
    argv: tuple[str, ...]

    @property
    def timed_out(self) -> bool:
        """Convenience flag: ``True`` when ``error == "timeout"``."""
        return self.error == "timeout"


@dataclass
class ActiveConnection:
    """Row parsed from ``nmcli -t -f NAME,TYPE,DEVICE connection show --active``."""

    name: str
    type_: str
    device: str


@dataclass
class DeviceRow:
    """Row parsed from ``nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device status``."""

    device: str
    type_: str
    state: str
    connection: str



def _split_terse(line: str) -> list[str]:
    """Split a nmcli ``-t`` line on ``:`` separators while respecting escapes.

    nmcli escapes special characters inside values with a leading backslash:

    * ``\\:``  -> ``:``
    * ``\\\\`` -> ``\\``
    * ``\\"`` -> ``"``

    A single-pass state machine is used instead of a regex split because a
    regex cannot distinguish ``\\:`` (escaped separator, part of the value)
    from ``\\\\:`` (escaped backslash followed by a real separator). The
    resulting fields are unescaped as part of the same pass.

    Residual limitation: if nmcli ever emits a literal backslash before an
    ordinary character (not one of ``:``, ``\\``, or ``"``), we preserve that
    backslash literally. This matches observed nmcli behavior and keeps the
    parser conservative.
    """

    fields: list[str] = []
    current: list[str] = []
    index = 0
    while index < len(line):
        char = line[index]
        if char == "\\" and index + 1 < len(line):
            next_char = line[index + 1]
            if next_char in (":", "\\", '"'):
                current.append(next_char)
                index += 2
                continue
        if char == ":":
            fields.append("".join(current))
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    fields.append("".join(current))

    # nmcli represents an intentionally empty CONNECTION value as the
    # literal two-character string "" after escaping; normalize that to
    # an empty string for callers.
    for idx, value in enumerate(fields):
        if value == '""':
            fields[idx] = ""
    return fields


def parse_active_connections(text: str) -> list[ActiveConnection]:
    r"""Parse terse ``NAME,TYPE,DEVICE`` rows into structured rows.

    Connection names may contain ``:``; nmcli escapes those as ``\:``.
    Fields after a trailing separator are treated as empty strings so rows
    with an unbound device (e.g. ``name:vpn:``) still parse positionally.
    """

    rows: list[ActiveConnection] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        fields = _split_terse(line)
        rows.append(
            ActiveConnection(
                name=fields[0] if len(fields) > 0 else "",
                type_=fields[1] if len(fields) > 1 else "",
                device=fields[2] if len(fields) > 2 else "",
            )
        )
    return rows


def parse_device_status(text: str) -> list[DeviceRow]:
    """Parse terse ``DEVICE,TYPE,STATE,CONNECTION`` rows into structured rows.

    Follows the same escaping rules as :func:`parse_active_connections`.
    """

    rows: list[DeviceRow] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        fields = _split_terse(line)
        rows.append(
            DeviceRow(
                device=fields[0] if len(fields) > 0 else "",
                type_=fields[1] if len(fields) > 1 else "",
                state=fields[2] if len(fields) > 2 else "",
                connection=fields[3] if len(fields) > 3 else "",
            )
        )
    return rows


class NmcliRunner:
    """Fixed-argv subprocess runner for nmcli and ip.

    All public methods are async and return :class:`NmcliResult`. Callers in
    ``vpn.py`` and ``routing.py`` should treat a non-ok result as a structured
    failure and surface it to the MCP layer.

    Constructor arguments allow tests to substitute binary names or timeout
    values without touching the module constants.
    """

    def __init__(
        self,
        nmcli_bin: str = NMCLI_BIN,
        ip_bin: str = IP_BIN,
        nmcli_timeout_s: float = NMCLI_TIMEOUT_S,
        ip_timeout_s: float = IP_TIMEOUT_S,
    ) -> None:
        """Initialize the runner.

        Args:
            nmcli_bin: Program name or path for ``nmcli``.
            ip_bin: Program name or path for ``ip``.
            nmcli_timeout_s: Timeout for nmcli operations.
            ip_timeout_s: Timeout for ``ip route get``.
        """

        self._nmcli_bin = nmcli_bin
        self._ip_bin = ip_bin
        self._nmcli_timeout_s = nmcli_timeout_s
        self._ip_timeout_s = ip_timeout_s

    async def _run(
        self,
        argv: list[str],
        timeout: float,
    ) -> NmcliResult:
        """Spawn a subprocess and return a structured result.

        Args:
            argv: Exact argv list. The first element is the program; the
                remainder are fixed constants plus the caller-provided value.
            timeout: Maximum time in seconds to wait for the process.

        Returns:
            A :class:`NmcliResult` describing success, non-zero exit,
            timeout, or spawn failure. Never raises for expected failure paths.
        """

        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as exc:
            return NmcliResult(
                ok=False,
                exit_code=None,
                stdout="",
                stderr=f"spawn failed: {exc}",
                error="spawn_failed",
                argv=tuple(argv),
            )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                pgid = getattr(proc, "pid", None)
                if pgid is not None:
                    try:
                        os.killpg(pgid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError, OSError):
                        pass
                try:
                    await proc.wait()
                except ProcessLookupError:
                    pass
            return NmcliResult(
                ok=False,
                exit_code=None,
                stdout="",
                stderr="",
                error="timeout",
                argv=tuple(argv),
            )
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        exit_code = proc.returncode
        if exit_code == 0:
            return NmcliResult(
                ok=True,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                error=None,
                argv=tuple(argv),
            )
        return NmcliResult(
            ok=False,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            error="exit",
            argv=tuple(argv),
        )

    async def general_status(self) -> NmcliResult:
        """Run ``nmcli general status``."""

        return await self._run(
            [self._nmcli_bin, "general", "status"],
            timeout=self._nmcli_timeout_s,
        )

    async def connection_show_active(self) -> NmcliResult:
        """Run ``nmcli -t -f NAME,TYPE,DEVICE connection show --active``."""

        return await self._run(
            [
                self._nmcli_bin,
                "-t",
                "-f",
                "NAME,TYPE,DEVICE",
                "connection",
                "show",
                "--active",
            ],
            timeout=self._nmcli_timeout_s,
        )

    async def connection_show(self, connection: str) -> NmcliResult:
        """Run ``nmcli connection show <connection>``."""

        return await self._run(
            [self._nmcli_bin, "connection", "show", connection],
            timeout=self._nmcli_timeout_s,
        )

    async def connection_up(self, connection: str) -> NmcliResult:
        """Run ``nmcli connection up <connection>``."""

        return await self._run(
            [self._nmcli_bin, "connection", "up", connection],
            timeout=self._nmcli_timeout_s,
        )

    async def connection_down(self, connection: str) -> NmcliResult:
        """Run ``nmcli connection down <connection>``."""

        return await self._run(
            [self._nmcli_bin, "connection", "down", connection],
            timeout=self._nmcli_timeout_s,
        )

    async def device_status(self) -> NmcliResult:
        """Run ``nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device status``."""

        return await self._run(
            [
                self._nmcli_bin,
                "-t",
                "-f",
                "DEVICE,TYPE,STATE,CONNECTION",
                "device",
                "status",
            ],
            timeout=self._nmcli_timeout_s,
        )

    async def route_get(self, probe: str) -> NmcliResult:
        """Run ``ip route get <probe>``.

        This lives in the runner (rather than routing.py) because it shares
        the same fixed-argv, no-shell, structured-timeout guarantees as the
        nmcli commands.
        """

        return await self._run(
            [self._ip_bin, "route", "get", probe],
            timeout=self._ip_timeout_s,
        )
