"""Shared test doubles for unit tests.

The shared ``FakeRunner`` is a stand-in for :class:`nmcli_mcp.nmcli.NmcliRunner`
that records every call and returns programmable responses.  It is used by both
``test_vpn.py`` and ``test_server.py``.

Convention:

* ``responses`` maps a method name (e.g. ``"connection_show_active"``) to either
  an :class:`NmcliResult` instance or an async callable.
* Callables are invoked as ``await response(method, args)`` where ``args`` is the
  tuple of positional arguments passed to the runner method.
* Missing responses default to an empty, successful :class:`NmcliResult`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from nmcli_mcp.nmcli import NmcliResult, NmcliRunner

Call = tuple[str, tuple[Any, ...]]
Response = NmcliResult | Callable[..., Awaitable[NmcliResult]]


def _ok(stdout: str = "", stderr: str = "") -> NmcliResult:
    """Build a successful fake result."""

    return NmcliResult(
        ok=True,
        exit_code=0,
        stdout=stdout,
        stderr=stderr,
        error=None,
        argv=(),
    )


def _fail(
    exit_code: int = 1,
    stdout: str = "",
    stderr: str = "",
    error: str | None = "exit",
) -> NmcliResult:
    """Build a fake result for a clean non-zero subprocess exit."""

    return NmcliResult(
        ok=False,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        error=error,
        argv=(),
    )


def _timeout() -> NmcliResult:
    """Build a fake result for a timed-out subprocess."""

    return NmcliResult(
        ok=False,
        exit_code=None,
        stdout="",
        stderr="",
        error="timeout",
        argv=(),
    )


class FakeRunner(NmcliRunner):
    """Programmable test double for ``NmcliRunner``.

    Records every call as ``(method, args)`` and resolves responses from the
    mapping supplied at construction.  The default response for an unconfigured
    method is an empty successful :class:`NmcliResult`.
    """

    def __init__(self, responses: Mapping[str, Response] | None = None) -> None:
        super().__init__()
        self.responses: Mapping[str, Response] = responses or {}
        self.calls: list[Call] = []

    async def general_status(self) -> NmcliResult:
        return await self._record("general_status", ())

    async def connection_show_active(self) -> NmcliResult:
        return await self._record("connection_show_active", ())

    async def connection_show(self, connection: str) -> NmcliResult:
        return await self._record("connection_show", (connection,))

    async def connection_up(self, connection: str) -> NmcliResult:
        return await self._record("connection_up", (connection,))

    async def connection_down(self, connection: str) -> NmcliResult:
        return await self._record("connection_down", (connection,))

    async def device_status(self) -> NmcliResult:
        return await self._record("device_status", ())

    async def route_get(self, probe: str) -> NmcliResult:
        return await self._record("route_get", (probe,))

    async def _record(self, method: str, args: tuple[Any, ...]) -> NmcliResult:
        self.calls.append((method, args))
        response = self.responses.get(method)
        if response is None:
            return _ok()
        if isinstance(response, NmcliResult):
            return response
        return await response(method, args)
