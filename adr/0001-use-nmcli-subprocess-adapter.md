# ADR-0001: Wrap nmcli as a no-shell subprocess adapter

## Status

Accepted

## Date

2026-09-17

## Context

The MCP server must control NetworkManager from Python on a headless Linux box (Python 3.10). NetworkManager exposes a D-Bus API, and `nmcli` is its official CLI client. The server's security posture requires that no tool input can become arbitrary command execution.

## Decision

Use `nmcli` (and `ip route get` for route verification) exclusively through fixed-argument subprocess execution (`create_subprocess_exec`, never a shell), with argv assembled only from constants and config-resolved values. All nmcli interaction lives in a single adapter module, so the choice remains reversible behind one boundary. We do not implement the D-Bus protocol in v1.

## Consequences

- Positive: minimal protocol surface; no D-Bus dependency or generated bindings; the no-shell rule keeps the LLM's input — a VPN id — from ever reaching a command line.
- Negative: terse-output parsing must handle nmcli's escaping (e.g. `\:` in connection names) and may drift across nmcli versions; process-spawn overhead per call; no D-Bus event subscription (state changes are only observed on demand).
- Follow-up: keep all parsing in `nmcli.py`/`routing.py`; a future D-Bus migration can replace the adapter without touching tools or orchestration.