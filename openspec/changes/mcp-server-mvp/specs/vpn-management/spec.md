## ADDED Requirements

### Requirement: VPN profiles are defined in TOML config

The server SHALL load VPN profiles from a TOML config file containing one `[[vpn]]` table per VPN with fields `id` (matching `^[a-z][a-z0-9-]*$`), `connection` (exact NetworkManager connection name, non-empty), and optional `expected_routes` (list of strict CIDR strings). The config path SHALL resolve in order: `$NMCLI_MCP_CONFIG`, then `$XDG_CONFIG_HOME/nmcli-mcp-server/config.toml`, then `~/.config/nmcli-mcp-server/config.toml`. The config SHALL be loaded and validated once at startup; any missing file, unreadable file, duplicate id, invalid id, or invalid CIDR (including host bits set, e.g. `10.8.0.5/24`) MUST fail startup with an error listing the paths tried or the invalid entry.

#### Scenario: Config found via XDG default path
- **GIVEN** no `$NMCLI_MCP_CONFIG` is set and `~/.config/nmcli-mcp-server/config.toml` exists with a valid `[[vpn]]` table
- **WHEN** the server starts
- **THEN** the profile is available to all tools without error

#### Scenario: Env override wins
- **GIVEN** `$NMCLI_MCP_CONFIG` points to a valid config file elsewhere
- **WHEN** the server starts
- **THEN** profiles are loaded from that file, not the XDG path

#### Scenario: Missing config fails startup
- **GIVEN** no config file exists at any resolved path
- **WHEN** the server starts
- **THEN** startup fails with an error listing every path that was tried

#### Scenario: Invalid config entry fails startup
- **GIVEN** a config where one `[[vpn]]` has `id = "Intetics"` (fails `^[a-z][a-z0-9-]*$`)
- **WHEN** the server starts
- **THEN** startup fails identifying the invalid entry

#### Scenario: Ambiguous CIDR fails startup
- **GIVEN** a config where `expected_routes` contains `10.8.0.5/24` (host bits set)
- **WHEN** the server starts
- **THEN** startup fails identifying the invalid route

### Requirement: Tools accept only configured VPN ids

Every VPN-addressing tool SHALL accept a single `name` argument that MUST be an id from the config-defined set. An unknown id MUST return a structured error listing the available ids; the model SHALL never supply NetworkManager connection names, arguments, or flags through any tool.

#### Scenario: Known id resolves to its connection
- **GIVEN** config maps `intetics` to connection `"Intetics VPN"`
- **WHEN** `vpn_status("intetics")` is called
- **THEN** the server inspects NetworkManager state for connection `"Intetics VPN"`

#### Scenario: Unknown id lists what is available
- **GIVEN** config defines only `intetics`
- **WHEN** `vpn_status("unknown-vpn")` is called
- **THEN** the result is a structured error naming `intetics` as the available id

### Requirement: vpn_list

`vpn_list` SHALL return the configured VPNs — for each, its `id` and its NetworkManager `connection` name — without invoking any subprocess.

#### Scenario: Lists configured profiles
- **GIVEN** config defines `intetics` and `testvpn`
- **WHEN** `vpn_list` is called
- **THEN** the result contains both ids with their connection names and `ok` is true

### Requirement: vpn_status

`vpn_status(name)` SHALL report whether the VPN's configured connection is currently active in NetworkManager and, when active, the device (tunnel interface) it is bound to.

#### Scenario: Active VPN reports connection and device
- **GIVEN** connection `"Intetics VPN"` is active on device `wg0`
- **WHEN** `vpn_status("intetics")` is called
- **THEN** the result reports connected as true and device `wg0`

#### Scenario: Inactive VPN reports disconnected
- **GIVEN** connection `"Intetics VPN"` is not active
- **WHEN** `vpn_status("intetics")` is called
- **THEN** the result reports connected as false

#### Scenario: Connection names containing colons are matched correctly
- **GIVEN** a configured connection whose name contains `:` is active
- **WHEN** `vpn_status` is called for it
- **THEN** the VPN is reported as connected and bound to the correct device

### Requirement: vpn_connect is idempotent

`vpn_connect(name)` SHALL activate the VPN's connection when inactive and return the verified current state. When the connection is already active, it MUST succeed without tearing down or recreating the connection.

#### Scenario: Inactive VPN is activated and verified
- **GIVEN** connection `"Intetics VPN"` is inactive
- **WHEN** `vpn_connect("intetics")` is called
- **THEN** `nmcli connection up` is invoked for that connection and the result reports the active state

#### Scenario: Already-active VPN is left untouched
- **GIVEN** connection `"Intetics VPN"` is already active
- **WHEN** `vpn_connect("intetics")` is called
- **THEN** no activation command runs and the result reports the current active state as success

### Requirement: vpn_disconnect

`vpn_disconnect(name)` SHALL deactivate the VPN's connection and report the verified post-state.

#### Scenario: Active VPN is deactivated
- **GIVEN** connection `"Intetics VPN"` is active
- **WHEN** `vpn_disconnect("intetics")` is called
- **THEN** the connection is deactivated and the result reports it as not connected

### Requirement: vpn_reconnect is health-aware

`vpn_reconnect(name)` SHALL first diagnose the VPN. When the VPN is already usable, it MUST succeed without changing any state. Otherwise it SHALL disconnect, connect, and re-diagnose, returning the final state.

#### Scenario: Healthy VPN is left alone
- **GIVEN** `vpn_diagnose("intetics")` reports the VPN as usable
- **WHEN** `vpn_reconnect("intetics")` is called
- **THEN** no disconnect or connect runs and the result reports success with the current state

#### Scenario: Unhealthy VPN is rebuilt
- **GIVEN** `vpn_diagnose("intetics")` reports the VPN as connected but not usable
- **WHEN** `vpn_reconnect("intetics")` is called
- **THEN** the connection is deactivated, activated, re-diagnosed, and the result reports the final state

### Requirement: vpn_diagnose verifies local usability

`vpn_diagnose(name)` SHALL determine local usability without opening connections to any remote endpoint: NetworkManager responsive, the profile exists, the connection is active, a tunnel device is bound, and every `expected_routes` CIDR resolves through that device per `ip route get` on a probe address derived from the CIDR (first host address; the network address itself for `/31` and `/32`). The result SHALL include per-route detail, and `usable` MUST be true only when all checks pass. For a CIDR of any prefix length, including single-host `/32` routes, the probe MUST stay inside the route's network.

#### Scenario: Fully usable VPN
- **GIVEN** the VPN is active on `wg0` and both `10.13.0.0/16` and `10.12.0.0/16` resolve via `wg0`
- **WHEN** `vpn_diagnose("intetics")` is called
- **THEN** the result reports usable true with each route ok and resolved to `wg0`

#### Scenario: Connected but route missing
- **GIVEN** the VPN is active on `wg0` but `10.12.0.0/16` resolves via the default route
- **WHEN** `vpn_diagnose("intetics")` is called
- **THEN** the result reports usable false with `10.12.0.0/16` marked not ok and the interface it actually resolved to

#### Scenario: Single-host route is probed inside its network
- **GIVEN** the VPN is active on `wg0` and config lists `10.8.0.5/32` expected to resolve via `wg0`
- **WHEN** `vpn_diagnose` runs the route check
- **THEN** the probe address is `10.8.0.5` itself and the route is reported ok

#### Scenario: Disconnected VPN is not usable
- **GIVEN** connection `"Intetics VPN"` is not active
- **WHEN** `vpn_diagnose("intetics")` is called
- **THEN** the result reports connected false, usable false, and the connection check as failed

### Requirement: network_status reports a device overview

`network_status` SHALL return one row per NetworkManager device with its device name, type, state, and the connection bound to it, parsed from nmcli device status.

#### Scenario: Devices are listed with state
- **GIVEN** NetworkManager manages `eth0` (connected) and `wg0` (connected to `"Test VPN"`)
- **WHEN** `network_status` is called
- **THEN** the result contains a row for each device including type, state, and bound connection

### Requirement: All tool results are structured JSON

Every tool SHALL return a structured JSON-compatible result. Successful results SHALL carry `ok: true` with the operation's observations. Failures SHALL carry `ok: false` with an error kind and details — including subprocess non-zero exits and timeouts. A timed-out subprocess MUST produce a structured timeout error, never a hanging tool call.

#### Scenario: Failure result is structured
- **GIVEN** `nmcli` exits non-zero when a tool runs
- **WHEN** the tool is called
- **THEN** the result contains `ok: false` with the error kind and stderr/stdout details

#### Scenario: Timeout produces a structured error
- **GIVEN** an nmcli invocation exceeds its timeout
- **WHEN** the tool call completes
- **THEN** the result contains a timeout error and the tool call returns rather than hanging

### Requirement: Tool inputs can never become arbitrary commands

The server SHALL execute `nmcli` and `ip` only through fixed-argument subprocess spawning without a shell, with argv assembled exclusively from constants and config-resolved values. No tool argument SHALL be able to alter the program invoked, add flags, or reach a shell.

#### Scenario: No input path reaches a shell
- **GIVEN** any sequence of tool calls with any argument values
- **WHEN** the server spawns a subprocess
- **THEN** the spawned program is `nmcli` or `ip` with an argv drawn only from fixed templates and config values

### Requirement: stdout is reserved for the MCP protocol

The server SHALL write only MCP protocol messages to stdout; all diagnostics and logging SHALL go to stderr, so the protocol stream is never corrupted.

#### Scenario: Logging stays off the protocol channel
- **GIVEN** the server is running under an MCP client
- **WHEN** the server logs an operational event
- **THEN** the log line appears on stderr and stdout carries only protocol messages