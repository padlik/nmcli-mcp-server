"""Shared pytest fixtures for the unit test suite."""

from __future__ import annotations

import pytest

import nmcli_mcp.server as nmcli_mcp_server


@pytest.fixture(autouse=True)
def _reset_server_service():
    """Clear the module-level server service after every test.

    ``nmcli_mcp.server`` exposes a module-level ``_service`` that tests inject
    via :func:`init_service`.  Without cleanup, a later test could accidentally
    reuse the runner/config from an earlier test.
    """

    yield
    nmcli_mcp_server._service = None
