## 1. Project scaffolding

- [ ] 1.1 Create `pyproject.toml`: requires-python `>=3.10`; runtime deps `mcp` and `tomli; python_version < '3.11'`; dev group `ruff`, `vulture`, `pytest`, `pytest-asyncio`; console script `nmcli-mcp = "nmcli_mcp.server:main"`; src layout. Run `uv sync` and confirm the environment resolves.
- [ ] 1.2 Create `src/nmcli_mcp/` package skeleton (`__init__.py`, `server.py`, `config.py`, `nmcli.py`, `routing.py`, `vpn.py`) and `tests/` layout; add `vulture_whitelist.py` (empty seed) so the AGENTS.md vulture command runs.

## 2. Config layer (`config.py`, design decision 2, ADR-0002)

- [ ] 2.1 Implement config resolution and loading: `$NMCLI_MCP_CONFIG` → `$XDG_CONFIG_HOME/nmcli-mcp-server/config.toml` → `~/.config/nmcli-mcp-server/config.toml`; stdlib `tomllib` with `except ImportError: import tomli as tomllib` shim; load once at startup.
- [ ] 2.2 Implement fail-fast validation: missing/unreadable file lists all paths tried; `id` must match `^[a-z][a-z0-9-]*$` and be unique; `connection` non-empty; `expected_routes` strict CIDRs (host-bits-set like `10.8.0.5/24` rejected). Error messages identify the offending entry.
- [ ] 2.3 Unit tests for config: each resolution path (env override wins, XDG default), every failure mode (missing, invalid id, duplicate id, ambiguous CIDR), and a valid multi-VPN config.

## 3. nmcli adapter (`nmcli.py`, design decision 3, ADR-0001)

- [ ] 3.1 Implement the runner: `asyncio.create_subprocess_exec` only (never a shell), fixed argv from constants plus config-resolved values, `asyncio.wait_for` timeouts (30s nmcli operations, 5s `ip route get`). Error mapping for both failure paths: timeout → structured `ok: false, error: "timeout"` result; non-zero exit → structured `ok: false` result with error kind and stderr/stdout details. Never a hang.
- [ ] 3.2 Implement terse-output parsing (`nmcli -t -f ...`) with `\:` unescaping before comparison. Unit tests include a connection name containing `:` and version-drift-safe parsing of `NAME,TYPE,DEVICE` / `DEVICE,TYPE,STATE,CONNECTION` shapes.

## 4. Route verification (`routing.py`, design decision 4)

- [ ] 4.1 Implement probe derivation: first host address (`network_address + 1`) for prefixes up to /30, the network address itself for `/31` and `/32`; parse `dev <iface>` from `ip route get <probe>`; route ok iff resolved interface equals the VPN's active interface.
- [ ] 4.2 Unit tests with mocked `ip route get` output: /16 through tunnel ok, /16 via default route not ok, `/32` probe stays inside its network, unparseable output → structured error.

## 5. VPN orchestration (`vpn.py`, design decision 5)

- [ ] 5.1 Implement `vpn_list` (config only, no subprocess) and `vpn_status` (active connection + bound device from `nmcli -t -f NAME,TYPE,DEVICE connection show --active`).
- [ ] 5.2 Implement idempotent `vpn_connect` (status first; already active → success without activation; else `nmcli connection up` then re-verify) and `vpn_disconnect` (down + verify post-state).
- [ ] 5.3 Implement health-aware `vpn_reconnect`: diagnose first; usable → success no-op; else disconnect → connect → re-diagnose.
- [ ] 5.4 Implement `vpn_diagnose` local check chain: NM responsive (`nmcli general status`) → profile exists → active → tunnel device bound → all `expected_routes` through that device; per-route detail; `usable = active AND interface AND all routes OK`.
- [ ] 5.5 Implement `network_status`: `nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device status` parsed to device rows.
- [ ] 5.6 Unit tests (mocked adapter) for the full behavior matrix: idempotent connect (both branches), health-aware reconnect (both branches), diagnose (usable, route-missing, disconnected), unknown id → structured error listing available ids.

## 6. MCP server layer (`server.py`, design decision 1, ADR-0004)

- [ ] 6.1 Implement FastMCP wiring: seven `@mcp.tool()` definitions delegating to orchestration; config loaded once at startup with fail-fast; logging configured stderr-only; `main()` runs `mcp.run(transport="stdio")`.
- [ ] 6.2 Unit tests: all seven tools registered; unknown-id error shape; every tool returns JSON-serializable dicts with `ok` true/false structure; smoke test that no tool path writes to stdout.

## 7. Lima VM test rig (design decision 7)

- [ ] 7.1 Script the one-time VM setup: install NetworkManager + WireGuard in `nsjail-test` with NetworkManager configured NOT to manage the default interface (stays on systemd-networkd); verify Lima connectivity survives.
- [ ] 7.2 Create the fake WireGuard "Test VPN" NM connection carrying the expected routes, plus the fixture config file inside the VM; script must be re-runnable.
- [ ] 7.3 Integration tests (marked `integration`, run in the VM via `uv run pytest -m integration` with `NMCLI_MCP_CONFIG` pointing at the fixture): `vpn_status`/`vpn_list` against the real NM, idempotent connect on the real WireGuard connection, `vpn_diagnose` route checks resolving through real `wg0`, `network_status` device rows.

## 8. Docs and final verification

- [ ] 8.1 Write README: install steps, production and Lima MCP client config shapes, TOML config reference, and the semantics of `usable` (locally routed ≠ end-to-end reachable, per ADR-0003 follow-up).
- [ ] 8.2 Run full local verification: `ruff check .`, `vulture . vulture_whitelist.py --min-confidence 80`, complete unit suite; run the integration suite inside the Lima VM. All green before completion.
- [ ] 8.3 Run `openspec validate mcp-server-mvp --type change --strict` and fix any reported issues before archive.