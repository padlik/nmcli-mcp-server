# ADR Review Manifest

- Status: completed
- Review date: 2026-09-17

## Review Summary

ADR review completed for this change. `<repo>/adr/` did not exist before this change (greenfield repository); the supersession graph is empty and the sequence starts at 0001. Design.md's Open Questions section identified three decisions meeting the durability bar (long-term architectural commitment, affecting future changes, not yet captured): they are recorded as ADRs 0001–0003. Review flagged stdio-only transport as the one remaining ADR-worthy commitment (previously only a rejected-alternative note); it is recorded as ADR-0004.

## In-Force ADRs Reviewed

- None prior - `<repo>/adr/` had no ADRs before this change; nothing in force constrained the design.

## New Durable ADRs Created

- `adr/0001-use-nmcli-subprocess-adapter.md` — nmcli (and `ip route get`) exclusively through fixed-argument `create_subprocess_exec`, never a shell; single adapter module; D-Bus rejected for v1. Grounded in design decision 3 (nmcli adapter) and risk item (parsing drift).
- `adr/0002-allowlist-vpn-ids-from-config.md` — tools accept only VPN ids from a fixed config-defined set resolved to connection names at call time; allowlist-by-construction against prompt injection; no raw connection names from the model. Grounded in design decision 2 (config) and the security boundary.
- `adr/0003-local-only-vpn-diagnosis.md` — `vpn_diagnose` verifies local usability only (NM responsive, profile, active, tunnel interface, routes via `ip route get`); no endpoint probing; end-to-end reachability is the agent's responsibility. Grounded in design decisions 4–5 (route verification, orchestration) and the rejected `connectivity_check`.
- `adr/0004-stdio-only-transport-no-daemon.md` — stdio is the only MCP transport; the client owns the server process lifecycle; no port/daemon/systemd unit exists to deploy. Grounded in the brief's HTTP/SSE rejection and the design's deployment view; promoted from rejected-alternative note at review's flag.