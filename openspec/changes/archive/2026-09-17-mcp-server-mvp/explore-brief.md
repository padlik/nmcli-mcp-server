# Explore brief — mcp-server-mvp

## What we are building

A Python MCP server (`nmcli-mcp-server`, package `nmcli_mcp`, console script `nmcli-mcp`) exposing semantic VPN-control tools over NetworkManager via `nmcli`. Transport: stdio, always. The LLM gets named VPN operations only — never arbitrary shell or nmcli access.

Production target: a Linux box (the only place NetworkManager/nmcli exists) running Python 3.10.12. Dev/test target: the local Lima VM `nsjail-test` (Ubuntu 26.04; NetworkManager is NOT installed there yet — installation is part of tasks).

## Settled design decisions

1. **Seven tools.** Read-only: `vpn_list`, `vpn_status`, `network_status`, `vpn_diagnose`. Mutating: `vpn_connect` (idempotent — already-connected is a success), `vpn_disconnect`, `vpn_reconnect` (health-aware: diagnose first; healthy → no-op success; unhealthy → disconnect, connect, then verify).
2. **`vpn_diagnose` is local-only.** Checks: nmcli responsive → profile exists → active → tunnel interface present → each `expected_routes` CIDR resolves through the tunnel via `ip route get <probe>` (parse `dev <iface>`). `usable = active AND interface AND all routes OK`. No TCP/endpoint probing — end-to-end reachability is the agent's job, not the server's.
3. **Config file.** TOML; runtime resolution order: `$NMCLI_MCP_CONFIG` → `$XDG_CONFIG_HOME/nmcli-mcp-server/config.toml` → `~/.config/nmcli-mcp-server/config.toml`. Missing config = fail fast at startup with the list of paths tried. No config-less mode.
4. **Config schema** (per-VPN table `[[vpn]]`): `id` (stable identifier exposed to the LLM; validated `^[a-z][a-z0-9-]*$`), `connection` (exact NetworkManager connection name), `expected_routes` (list of CIDRs, optional). No `connectivity_targets` — removed with connectivity_check.
5. **Identity resolution only.** `id` → `connection` lookup from config; unknown id → structured error listing available ids. The LLM can never supply connection names, arguments, or flags. Security posture is allowlist-by-construction: the only LLM-controlled input anywhere is a VPN id from a fixed set.
6. **Python 3.10 compatibility.** Dependency: `tomli; python_version < '3.11'` with the `import tomllib except ImportError → tomli as tomllib` shim (decision A from exploration; the project's only dependency besides the MCP SDK). Avoid `asyncio.timeout()` (use `asyncio.wait_for`), `StrEnum`, `typing.Self`.
7. **Subprocess discipline.** `asyncio.create_subprocess_exec` only — never a shell. Fixed argv constructed only from config values (connection name) and constants. Timeouts on every external call: nmcli ops 30s, `ip route get` 5s. Structured timeout errors, never a hang. stderr-only logging (stdout is the MCP protocol channel).
8. **Deployment.** Console script `nmcli-mcp` = `nmcli_mcp.server:main`. Client spawns e.g. `uv --directory /opt/nmcli-mcp-server run nmcli-mcp` (production) or `limactl shell nsjail-test -- uv --directory ~/nmcli-mcp-server run nmcli-mcp` (Lima test rig — `limactl shell` relays stdio over SSH, so the same stdio server runs unchanged inside the VM).
9. **Test strategy.** Unit tests on the Mac (mocked subprocess output; no nmcli needed) + integration tests inside the Lima VM against a fake WireGuard NM connection ("Test VPN") with expected routes attached — real nmcli, real `ip route get` through `wg0`, no real corporate network. Integration tests use a fixture config via `NMCLI_MCP_CONFIG`. No `~/.config` pollution.
10. **Tool outputs are structured JSON** (dicts), including on failure (`ok: false`, `error`, `details`) so the agent can reason over results rather than parse prose.

## Rejected alternatives (and why)

- **NetworkManager D-Bus API** — unnecessary protocol work for v1; nmcli is the official CLI, appropriate for headless boxes.
- **YAML config** — PyYAML drags a dependency tree; TOML is comment-friendly and near-stdlib.
- **JSON config** — no comments; hand-edited VPN profiles suffer.
- **`connectivity_check` tool + `connectivity_targets` config** — dropped by user decision: end-to-end probing is achievable by other methods and is not this server's goal; also the only open-ended input in the surface (prompt-injection TCP probing) — removing it makes the security posture allowlist-by-construction.
- **Arbitrary connection names as tool input** — attack surface; the id→connection mapping from config prevents it.
- **Config-less mode** — a server answering "unknown VPN" to everything is a useless failure; fail fast instead.
- **uv-managed newer Python on the box** — the stated target is the box's Python 3.10.12; A was chosen over forcing a runtime upgrade.
- **HTTP/SSE transport** — stdio only; the client spawns the process, no daemon/port/systemd exists to deploy.
- **uv tool install / XDG_DATA_HOME default for `--directory`** — explored and withdrawn by user; deployment stays clone-and-run with explicit `--directory`.

## Key cross-module data flows

```
MCP client (stdio) -> nmcli_mcp.server: tool call with vpn id
server -> config: resolve id -> connection name, expected_routes  [startup: load/validate config once]
server -> nmcli.py adapter: run fixed-argv nmcli, return (rc, stdout, stderr) under timeout
server -> routing.py: ip route get probe -> parse dev iface
server -> vpn.py: orchestrate status/diagnose/connect/reconnect from adapter+config results
server -> MCP client: structured dict result
```

## Open questions

None blocking. Deferred (explicitly out of scope for v1): publishing to PyPI (`uvx` install shape), agent-side policy gating of mutating tools (belongs to the controller), multi-VPN-parallel operation support.