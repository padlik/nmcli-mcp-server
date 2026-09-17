## Context

Greenfield repository: no source code exists yet. The product (per proposal) is a Python MCP server wrapping Linux NetworkManager via `nmcli`, giving LLM agents semantic VPN tools instead of shell access.

Verified environment facts:
- Production target: a Linux box running NetworkManager, Python 3.10.12 (this pins the language level — no 3.11+ stdlib).
- Dev machines are macOS — `nmcli` does not exist there. The Lima VM `nsjail-test` (Ubuntu 26.04, aarch64) is the integration test rig; NetworkManager and WireGuard are NOT installed there yet (verified) and will be installed as part of this change.
- No ADRs exist yet (`<repo>/adr/` is empty); no prior decisions constrain this design.

**Diagram conventions (assumptions, per c4-diagrams gates):** ASCII format, lightweight C4-inspired rigor — only levels that answer real questions.

### System context (level 1)

```
+-------------+   MCP / stdio   +----------------------+  subprocess exec  +----------------+
| LLM agent   | --------------> | nmcli-mcp (nmcli_mcp)| ----------------> | nmcli / ip      |
| (MCP client)| <-------------- |  semantic VPN tools  | <---------------- |                 |
+-------------+  structured JSON+----------------------+  rc/stdout/stderr +--------+--------+
                                                                       | talks to
                                                                       v
                                                              NetworkManager /
                                                               Linux networking
```

Boundary: the LLM's only inputs are VPN ids from config — it cannot reach nmcli/ip except through the seven fixed tools.

### Component view (level 3, inside the container)

```
src/nmcli_mcp/
+------------------------------------------------------+
| server.py   MCP layer (MCPServer): tool defs, results   |
|     |                                                |
| config.py   TOML load, id->connection map, validate  |
|     |                                                |
| vpn.py      orchestration: status, diagnose,         |
|             idempotent connect, health-aware reconnect|
|     |                     |                           |
|     v                     v                           |
| nmcli.py    fixed-argv    routing.py   probe->iface    |
|             nmcli + ip runner           (pure math)     |
+------------------------------------------------------+
   tests/       unit: mocked exec (runs on Mac)
                integration: real nmcli in Lima VM (-m integration)
```

Responsibility split: `server.py` = safe interface only; `nmcli.py` = sole nmcli adapter; `routing.py` = route verification; `vpn.py` = orchestration/diagnosis; agent = reasoning (outside this system).

### Deployment view (the one non-obvious part)

```
PRODUCTION Linux box                     DEV/TEST (macOS host)
+-----------------------+                +--------------------------------+
| MCP client spawns:    |                | MCP client spawns:             |
| uv --directory /opt/  |                | limactl shell nsjail-test --   |
|   nmcli-mcp-server    |                |   uv --directory ~/nmcli-      |
|   run nmcli-mcp       |                |     mcp-server run nmcli-mcp   |
+-----------+-----------+                +---------------+----------------+
            |  stdio is the protocol    | limactl relays stdio over SSH;
            v                           | server runs INSIDE the VM, unchanged
+-----------------------+                +--------------------------------+
| nmcli_mcp             |                | nmcli_mcp (same code)          |
| NetworkManager (real) |                | NetworkManager + fake WireGuard|
+-----------------------+                | "Test VPN" (to be installed)   |
                                         +--------------------------------+
```

No daemon, port, or systemd unit exists to deploy: the client owns the server's lifecycle; stdio is the only transport.

## Goals / Non-Goals

**Goals:**
- Seven MCP tools (4 read-only, 3 mutating) with structured JSON results, including failures.
- Allowlist-by-construction security: the only LLM-controlled input anywhere is a VPN id from a fixed config-defined set.
- Local-only usability diagnosis: "connected" and "usable" are different answers, computed without any endpoint probing.
- Python 3.10 compatibility; minimal dependencies (`mcp`, conditional `tomli`).
- Unit tests runnable on macOS; integration tests runnable in the Lima VM.

**Non-Goals:**
- End-to-end reachability probing (`connectivity_check`-style tools) — the agent verifies reachability with its own tools.
- PyPI publishing / `uvx` install shape.
- Agent-side policy gating of mutating tools (belongs to the MCP client/controller).
- Multi-VPN-parallel operation support.
- The NetworkManager D-Bus protocol (nmcli subprocess is the adapter).

## Decisions

1. **MCP layer**: Python MCP SDK high-level server — installed SDK is `mcp` 2.x, where the v1 `FastMCP` class was renamed to `MCPServer` (`mcp.server.mcpserver.MCPServer`); stdio transport, tools return plain dicts (serialized as JSON content by the SDK). Console script `nmcli-mcp` = `nmcli_mcp.server:main`. Logging goes to **stderr only** (stdout is the protocol channel; one stray print corrupts the stream).

2. **Config** (`config.py`): TOML `[[vpn]]` tables with fields `id` (string, must match `^[a-z][a-z0-9-]*$`), `connection` (non-empty string, exact NM connection name), `expected_routes` (optional list of CIDR strings). Resolution: `$NMCLI_MCP_CONFIG` → `$XDG_CONFIG_HOME/nmcli-mcp-server/config.toml` → `~/.config/nmcli-mcp-server/config.toml`. Loaded and validated **once at startup**; failure = fail fast with the list of paths tried (no config-less mode). Validation errors (bad id regex, duplicate ids, unparseable routes) also fail fast at startup. Parsing uses stdlib `tomllib` with a `try/except ImportError: import tomli as tomllib` shim; dependency marker `tomli; python_version < '3.11'`.

3. **nmcli adapter** (`nmcli.py`): all invocations use `asyncio.create_subprocess_exec` (never a shell) with fixed argv assembled from constants plus config-resolved values. Timeouts via `asyncio.wait_for`: 30s for nmcli operations, 5s for `ip route get`. Timeout → structured error dict (`ok: false, error: "timeout", ...`), never a hang. Parsing uses `nmcli -t -f <fields>` terse output; because `:` is the terse separator **and** connection names may contain `:`, fields are unescaped (nmcli escapes `:` as `\:` in terse values) before comparison — match on the unescaped connection name. Unknown VPN id → error dict listing available ids.

4. **Route verification** (`routing.py`): per configured CIDR, derive a deterministic probe: first host address of the network (`ipaddress.ip_network(cidr).network_address + 1`) for prefixes up to /30; for /31 and /32 (no usable host range) probe the `network_address` itself. Parse the `dev <iface>` token from `ip route get <probe>` output and pass the route check iff the resolved interface equals the VPN's active interface. Execution note: `routing.py` is pure probe math and parsing; the `ip route get` subprocess itself runs through the single subprocess adapter in `nmcli.py` (decision 3's single-adapter invariant), driven by `vpn.py`. CIDRs are parsed with stdlib defaults (strict): host-bits-set entries (e.g. `10.8.0.5/24`) are invalid config and fail fast at startup, per decision 2.

5. **Orchestration** (`vpn.py`):
   - `vpn_status`: `nmcli -t -f NAME,TYPE,DEVICE connection show --active` → is the configured connection active, and on which device.
   - `vpn_connect` (idempotent): status first; already active → return current state; else `nmcli connection up <connection>` then re-verify active state.
   - `vpn_disconnect`: `nmcli connection down <connection>`, verify inactive.
   - `vpn_reconnect` (health-aware): full diagnose first; usable → success no-op; else disconnect → connect → re-diagnose.
   - `vpn_diagnose` (local-only): NM responsive (`nmcli general status`) → profile exists (`nmcli connection show <connection>`) → active → device/tunnel interface present → every `expected_routes` CIDR resolves through that interface (decision 4). Result includes per-route detail; `usable = active AND interface AND all routes OK`.
   - `vpn_list`: ids + connection names from config (no subprocess).
   - `network_status`: `nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device status` parsed to device rows.

6. **Python 3.10 discipline**: no `asyncio.timeout()`, `StrEnum`, or `typing.Self`; `from __future__ import annotations`; stdlib `ipaddress` for route math.

7. **Lima test rig** (one-time setup, scripted): install NetworkManager + WireGuard in `nsjail-test` — with NetworkManager configured to NOT manage the VM's default interface (Ubuntu 26.04 runs systemd-networkd; Lima connectivity must not break). Create a fake WireGuard connection "Test VPN" carrying the expected routes. Integration tests run in the VM via `uv run pytest -m integration` with a fixture config injected through `NMCLI_MCP_CONFIG`.

## Risks / Trade-offs

- **[nmcli terse parsing with `:` in names]** → unescape fields before comparing (nmcli escapes as `\:`); unit tests include a `:`-in-name fixture. Residual risk: parsing drift across nmcli versions → adapter is the single place to fix.
- **[NetworkManager install could break Lima VM networking]** → NM manages only the WireGuard test connection; default interface stays with systemd-networkd. Verified in the rig before any integration test runs.
- **[VPN secrets prompt blocks `connection up`]** → assumption: profiles are system connections with stored secrets (standard for corp VPNs); non-tty nmcli fails fast rather than prompting, and the 30s timeout bounds the worst case.
- **[`ip route get` needs root?]** → no, route lookup is unprivileged; the integration fixture proves it in the same unprivileged conditions the server runs under.
- **[macOS cannot run integration tests]** → accepted: unit tests (mocked exec) run everywhere; integration runs only inside the VM. Risk of fixture drift → fixture creation is scripted, re-runnable.
- **[Two config sources of truth (XDG vs env override)]** → env override exists primarily for tests; production uses exactly one path. Documented in README.

## Migration Plan

Fresh package; nothing to migrate. Rollout: clone repo on target box → `uv sync` → add MCP client config entry (bare `uv --directory <path> run nmcli-mcp`). Rollback: remove the client config entry (server is a child process; nothing persists). No external state; config file is user-owned.

## Open Questions

None blocking. Deferred items are captured as Non-Goals (PyPI publishing, controller-side policy, parallel VPN operations). No in-force ADRs exist to revisit; the durable decisions here (no-shell adapter, id allowlist, local-only diagnosis) are candidates to distill into ADRs at the adr stage.