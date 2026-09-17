# ADR-0004: Use stdio as the only MCP transport; no daemon

## Status

Accepted

## Date

2026-09-17

## Context

The server's only consumer is an agent (MCP client) running on the same host, which spawns tool servers as child processes. MCP alternatively supports HTTP/SSE transports; a long-running daemon would add a port, a systemd unit, and lifecycle management. The Lima test rig works precisely because stdio can be relayed over SSH (`limactl shell`) with zero transport code — the same server runs unchanged inside the VM.

## Decision

Stdio is the only transport. The MCP client owns the server's lifecycle: it spawns the console script on demand and terminates it on disconnect. There is no port, no daemon, and no systemd unit to deploy; "deployment" is an entry in the client's config that launches the process. Logging goes to stderr so stdout stays a clean protocol channel.

## Consequences

- Positive: nothing to operate or secure beyond the process itself (no port exposure); identical client config shape for production and the Lima test rig; a stray stdout print would corrupt the protocol, which the stderr-only rule prevents.
- Negative: one server instance per client process (no sharing across concurrent clients); no remote access without an SSH-style relay; server lifetime is tied to the client session.
- Follow-up: if remote access is ever needed, that transport addition must be a new decision superseding this ADR.