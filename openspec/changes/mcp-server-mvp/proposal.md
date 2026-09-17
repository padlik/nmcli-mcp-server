## Why

LLM agents controlling VPNs today either get arbitrary shell access (unsafe, prompt-injection-prone) or nothing at all. This change introduces a Python MCP server that exposes NetworkManager VPN operations as semantic, allowlisted tools — `vpn_connect("intetics")` instead of raw `nmcli` — so an agent can safely observe network state, bring a VPN up, and verify it is actually usable, without any path to arbitrary command execution.

## What Changes

- New Python package `nmcli_mcp` (console script `nmcli-mcp`) implementing an MCP server over stdio, wrapping `nmcli` (and `ip route get` for route verification) through fixed-argument subprocess execution — no shell, ever.
- Seven MCP tools: `vpn_list`, `vpn_status`, `vpn_diagnose`, `network_status` (read-only; NetworkManager device and connection overview), `vpn_connect` (idempotent), `vpn_disconnect`, `vpn_reconnect` (health-aware) (mutating).
- TOML-based VPN profile config resolved from XDG paths (`$NMCLI_MCP_CONFIG` env override → `$XDG_CONFIG_HOME/nmcli-mcp-server/config.toml` → `~/.config/nmcli-mcp-server/config.toml`); startup fails fast when missing. The LLM only ever sees stable VPN ids; ids resolve to NetworkManager connection names from config.
- `vpn_diagnose` verifies local usability only: active state, tunnel interface, and expected routes resolving through the tunnel. End-to-end reachability probing is deliberately out of scope (the agent verifies reachability with its own other tools).
- Every tool returns structured JSON results, including failures (`ok: false` plus error details), so agents reason over observations instead of parsing prose.
- Timeouts on all subprocess calls, with structured timeout errors rather than hangs; stderr-only logging (stdout is the MCP channel).
- Lima VM test rig setup: NetworkManager + WireGuard installed in `nsjail-test`, fake "Test VPN" WireGuard connection with expected routes for integration tests.
- New project scaffolding: `pyproject.toml` (Python >= 3.10; `tomli` conditional dependency for Python < 3.11), src/tests layout, ruff + vulture config.

**Non-goals** (explicitly deferred): end-to-end reachability probing (`connectivity_check`-style tools); publishing to PyPI; agent-side policy gating of mutating tools (belongs to the MCP client/controller, not this server); multi-VPN-parallel operation support.

## Capabilities

### New Capabilities

- `vpn-management`: Semantic, allowlisted VPN control over NetworkManager — the seven MCP tools, id→connection resolution from TOML config, idempotent connect, health-aware reconnect, local-only VPN diagnosis (active state, tunnel interface, route verification), structured JSON tool results, and the no-shell subprocess discipline with timeouts.

### Modified Capabilities

(none — greenfield repository; no specs exist yet)

## Impact

- **Code**: entire package is new (`src/nmcli_mcp/`: server, config, nmcli adapter, routing, vpn orchestration; `tests/`).
- **Dependencies**: `mcp` (Python MCP SDK) and `tomli` on Python < 3.11 — the only runtime dependencies; dev tooling via uv (`ruff`, `vulture`, `pytest`, `pytest-asyncio`).
- **Systems**: Linux hosts running NetworkManager (nmcli + iproute2 required); macOS dev machines cannot run the server — verification happens through the Lima VM `nsjail-test`, which needs NetworkManager and WireGuard installed as part of this change.
- **Docs**: new README describing installation, the MCP client config shapes (local and Lima), and the TOML config format.