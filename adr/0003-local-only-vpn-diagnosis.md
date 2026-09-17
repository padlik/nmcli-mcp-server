# ADR-0003: Diagnose VPN usability locally, without endpoint probing

## Status

Accepted

## Date

2026-09-17

## Context

NetworkManager reporting a VPN "connected" does not mean it is usable — routes may be missing or point at the wrong interface. The obvious check is end-to-end reachability (TCP probing of corporate endpoints), but the agent already has other tools to verify reachability, endpoint probing was the only open-ended input in the tool surface, and it is not this server's purpose.

## Decision

`vpn_diagnose` verifies local usability only: NetworkManager responsive, profile exists, connection active, tunnel interface present, and every configured `expected_routes` CIDR resolving through that interface via `ip route get`. It never opens connections to remote endpoints. End-to-end reachability is the agent's responsibility, verified with its own tools.

## Consequences

- Positive: deterministic diagnosis with no external dependencies; keeps the tool surface closed (no host/port input anywhere); testable in a VM with a fake WireGuard tunnel and no corporate network.
- Negative: "usable" means locally routed, not end-to-end reachable — a dead peer or firewalled endpoint still reports usable; agents must not treat `usable: true` as a reachability guarantee.
- Follow-up: document the semantics of `usable` in the README so agent authors compose it with their own reachability checks.