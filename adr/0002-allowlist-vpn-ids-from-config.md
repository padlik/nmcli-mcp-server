# ADR-0002: Allowlist VPN ids resolved from config, never raw connection names

## Status

Accepted

## Date

2026-09-17

## Context

The server exposes VPN control to LLM agents, which may be subject to prompt injection. Accepting arbitrary NetworkManager connection names — or any free-form arguments — as tool input would turn the tool surface into an attack path, and would leak the host's full connection inventory into the model's context.

## Decision

Tools accept only a stable VPN id from a fixed, config-defined set (`[[vpn]]` tables in TOML, ids matching `^[a-z][a-z0-9-]*$`). The server resolves id → NetworkManager connection name at call time from config loaded once at startup; unknown ids return a structured error listing available ids. Connection names, arguments, and flags are never accepted from the model.

## Consequences

- Positive: allowlist-by-construction — the only model-controlled input in the entire surface is one member of a small fixed set; no validation heuristics needed beyond a dict lookup; connection inventory never leaks.
- Negative: adding or renaming a VPN requires a config edit and server restart rather than a tool call; config is a second artifact to keep correct.
- Follow-up: config validation (id regex, duplicate ids, CIDR syntax) fails fast at startup so a bad config cannot degrade the allowlist.