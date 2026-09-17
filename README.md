# nmcli-mcp-server

MCP server exposing NetworkManager VPN operations (`nmcli`) as semantic, allowlisted tools.

Instead of giving an LLM agent arbitrary shell access, this server exposes seven fixed
tools — `vpn_connect("intetics")` instead of raw `nmcli`. All subprocess execution uses
fixed-argument `create_subprocess_exec` (never a shell), and the only LLM-controlled
input anywhere is a VPN id from a config-defined set.

Requires Linux with NetworkManager (`nmcli`) and iproute2 (`ip`).

## Installation

```bash
git clone <repo-url> nmcli-mcp-server
cd nmcli-mcp-server
uv sync
```

No daemon, port, or systemd unit exists: the MCP client owns the server process
lifecycle and `stdio` is the only transport (ADR-0004).

## MCP client configuration

**Production (Linux host):**

```json
{
  "mcpServers": {
    "nmcli": {
      "command": "uv",
      "args": ["--directory", "/opt/nmcli-mcp-server", "run", "nmcli-mcp"]
    }
  }
}
```

**Lima VM (from a macOS host — `limactl` relays stdio over SSH; the server runs
inside the VM, unchanged):**

```json
{
  "mcpServers": {
    "nmcli": {
      "command": "limactl",
      "args": [
        "shell",
        "nsjail-test",
        "--",
        "uv",
        "--directory",
        "/home/paulpronko.guest/nmcli-mcp-server",
        "run",
        "nmcli-mcp"
      ]
    }
  }
}
```

## TOML config reference

Resolved at startup, in order (first match wins):

1. `$NMCLI_MCP_CONFIG`
2. `$XDG_CONFIG_HOME/nmcli-mcp-server/config.toml`
3. `~/.config/nmcli-mcp-server/config.toml`

```toml
[[vpn]]
id = "intetics"                 # tool-facing id; must match ^[a-z][a-z0-9-]*$, unique
connection = "Intetics VPN"     # exact NetworkManager connection name
expected_routes = [             # optional; strict CIDRs (host bits rejected)
  "10.13.0.0/16",
  "10.12.0.0/16",
  "fd00:abcd::/64",
]
```

The config is loaded and validated **once at startup**. A missing file (all candidate
paths listed in the error), an unreadable/unparseable file, an invalid or duplicate id,
an empty `connection`, or a non-strict CIDR (e.g. `10.8.0.5/24`) fails startup
immediately. The `NMCLI_MCP_CONFIG` override exists primarily for tests; production
should use exactly one XDG path.

VPN profiles are expected to be NetworkManager system connections with stored secrets
(non-tty `nmcli` fails fast rather than prompting for them).

## Tools

| Tool | Kind | Behavior |
| --- | --- | --- |
| `vpn_list()` | read-only | Configured ids and connection names (no subprocess). |
| `vpn_status(name)` | read-only | Active state and bound device for the connection. |
| `vpn_diagnose(name)` | read-only | Local usability check chain (see below). |
| `network_status()` | read-only | One row per NetworkManager device. |
| `vpn_connect(name)` | mutating | Idempotent: no-op when already active, else `nmcli connection up` + re-verify. |
| `vpn_disconnect(name)` | mutating | `nmcli connection down` + verified post-state. |
| `vpn_reconnect(name)` | mutating | Health-aware: skips work when already usable, else disconnect → connect → re-diagnose. |

`name` must be an id from the config; an unknown id returns a structured error listing
the available ids. Every tool returns structured JSON: `ok: true` with observations, or
`ok: false` with an error kind and details (subprocess non-zero exits and timeouts
included — a timed-out call returns a structured timeout error, never a hang). All
logging goes to stderr; stdout carries only MCP protocol messages.

## Semantics of `usable`

`vpn_diagnose` verifies **local** usability only (ADR-0003): NetworkManager responsive,
profile exists, connection active, a tunnel device is bound, and every
`expected_routes` CIDR resolves through that device per `ip route get` on a probe
derived from the CIDR (first host address; the network address itself for `/31`–`/32`).
`usable: true` requires all checks to pass.

**Locally routed ≠ end-to-end reachable.** `usable: true` means the kernel would send
traffic for the expected routes into the tunnel. It does **not** prove the peer is
alive or any endpoint answers: a dead peer or firewalled endpoint still reports
usable. Agents must compose `vpn_diagnose` with their own reachability checks before
concluding a VPN works.

## Integration testing in the Lima VM

The test rig lives in `scripts/` (re-runnable):

```bash
./scripts/lima_setup.sh     # NetworkManager + WireGuard; NM pinned unmanaged on eth0
./scripts/lima_fixture.sh   # fake WireGuard "Test VPN" + fixture config.toml
```

`lima_setup.sh` configures NetworkManager to **not** manage the VM's default interface
(`eth0` stays on systemd-networkd, so Lima connectivity survives) and installs a
polkit rule allowing `netdev` members to control networking over SSH sessions.
`lima_fixture.sh` recreates the "Test VPN" connection carrying the expected routes and
writes the fixture config under the VM user's home.

Integration tests run inside the VM:

```bash
limactl shell nsjail-test -- bash -lc '
  cd ~/nmcli-mcp-server &&
  ~/.local/bin/uv run pytest -m integration'
```

The suite expects `NMCLI_MCP_CONFIG` to point at the fixture config
(`~/nmcli-mcp-fixture/config.toml` is the default written by `lima_fixture.sh`).

## Development

```bash
uv run pytest tests/unit -q   # unit tests (mocked exec) run anywhere
uv run ruff check .
uv run vulture . vulture_whitelist.py --min-confidence 80
```

Python >= 3.10 (stdlib `tomllib` on 3.11+, `tomli` below). Architecture:
`server.py` (MCP layer) → `vpn.py` (orchestration) → `nmcli.py` (sole nmcli/ip
adapter) + `routing.py` (route-probe math) + `config.py` (TOML profiles).

## License

MIT