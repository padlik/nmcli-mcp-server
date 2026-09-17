# Review log — mcp-server-mvp

## proposal Round 1 — 2026-09-17

Reviewer: @openspec-reviewer (baseline: explore-brief.md). Verdict: high fidelity, one serious issue.

### 🔴 Fixed
- GitHub Actions CI appeared in "What Changes" and "Impact" — scope drift invented beyond the brief (brief's test strategy is Mac unit tests + Lima VM integration tests; AGENTS.md mandates local checks, never CI) → removed from the proposal.

### 🟡 Addressed
- `network_status` listed but behavior undefined anywhere → defined in proposal as NetworkManager device/connection overview; full scenarios to land in specs.
- Deferred/out-of-scope items from brief not carried → added Non-goals note: end-to-end reachability probing, PyPI publishing, agent-side policy gating (belongs to controller), multi-VPN-parallel operation.
- Implementation detail leaking into "What Changes" → removed `asyncio.create_subprocess_exec` API name and exact timeout values (30s/5s stay in brief/design); behavioral constraints ("no shell, ever", timeouts with structured errors) kept.
- `"intetics"` example id → kept deliberately: sourced from the user's original design document, matches `^[a-z][a-z0-9-]*$`; not an invention.
- Config schema specifics (fields, id regex, unknown-id error listing available ids) → deliberately deferred to the specs artifact.

### 🔴 Outstanding
- None unfixed — Round 2 dispatched to verify the fixes before freezing.

## proposal Round 2 — 2026-09-17

Reviewer: @openspec-reviewer (same session, baseline: explore-brief.md). Verdict: **passes, no 🔴 — proposal.md frozen.**

### 🔴 Outstanding
- None.

### 🟡 Notes (carried forward to later batches, non-blocking)
- `network_status` description is lightweight at proposal level — specs artifact must pin down concrete scenarios/output shape.
- Config schema fields, id regex, unknown-id error behavior — land them in the specs artifact.

### ✅ Fixed
- CI scope drift fully removed (no trace anywhere).
- network_status defined; Non-goals note added; why-not-how boundary restored.
- "intetics" example accepted (sourced from user's design doc).

## design Round 1 — 2026-09-17

Reviewer: @openspec-reviewer (same session; baseline: explore-brief.md; frozen proposal.md as consistency baseline). Verdict: **no 🔴 — design.md frozen.**

### 🔴 Outstanding
- None.

### 🟡 Fixed (declarative, post-verdict — boundary condition + wording)
- Probe derivation broke for /32//31 host routes (`network_address + 1` escapes the network) → decision 4 now probes the network_address itself for /31 and /32, and pins strict CIDR parsing (host-bits-set entries fail fast at startup per decision 2).
- "CI-like conditions" phrasing (CI was removed from proposal) → reworded to "the same unprivileged conditions the server runs under."

### ✅ Verified
- All ten brief decisions realized; C4 ASCII diagrams (system context, component, deployment) present and load-bearing; allowlist-by-construction security intact; Python 3.10 discipline holds (no 3.11+ APIs as mechanisms); idempotent connect / health-aware reconnect / local-only diagnose coherent; Lima rig scoping (NM manages only Test VPN, default iface stays on systemd-networkd) sound.

## adr Round 1 — 2026-09-17

Reviewer: @openspec-reviewer (same session; baselines: explore-brief.md, frozen proposal.md + design.md). Verdict: **no 🔴 — ADR batch frozen** (with one post-verdict addition, below).

### 🔴 Outstanding
- None.

### 🟡 Addressed
- stdio-only/no-daemon flagged as the remaining ADR-worthy candidate left only as a rejected-alternative note → consciously promoted to ADR-0004 (stdio-only-transport-no-daemon.md) and added to the manifest; TOML config and the Python 3.10 pin deliberately stay out (preference / externally imposed constraint, not architecture choices).
- ADR-0001 H1 vs filename slug wording mismatch → accepted as cosmetic; manifest references filenames.

### ✅ Verified
- Manifest thin and non-duplicating; location/naming/sequence correct (top-level adr/, NNNN-kebab-title, monotonic from 0001); MADR-short structure conforms to preferences.md (madr-minimal, set per schema mandate); one decision per ADR with honest downsides; all three grounded in frozen design/brief with no contradictions; durability bar met.

## specs Round 1 — 2026-09-17

Reviewer: @openspec-reviewer (same session; baselines: explore-brief.md, frozen proposal/design/adr). Verdict: **no 🔴 — specs frozen.**

### 🔴 Outstanding
- None.

### 🟡 Addressed
- `:`-in-name scenario THEN asserted mechanics ("matched after unescaping") → reframed to observable outcome (reported connected + correct device).

### 🟡 Accepted as-is
- "nmcli connection up is invoked" and "/32 probe is 10.8.0.5 itself" THENs kept — reviewer deemed them defensible pins of the idempotency and /32-probe contracts.
- No-shell property-style scenario kept — security invariant resists a single concrete example.
- "Unreadable file fails startup" and strict host-bits CIDR noted as consistent refinements of design decisions 2/4 (traceability only).

### ✅ Verified
- Delta structure (ADDED-only, greenfield), capability path matches frozen proposal, 4-hashtag scenarios (25), every requirement ≥1 scenario, SHALL/MUST normative verbs, all seven tools + config schema + cross-cutting requirements covered, no contradictions with design decisions 1-7 or ADRs 0001-0004, no invented scope. Archive parsing safe.

## tasks Round 1 — 2026-09-17

Reviewer: @openspec-reviewer (same session; baselines: explore-brief.md, all five frozen artifacts). Verdict: **no 🔴 — tasks frozen; artifact set complete.**

### 🔴 Outstanding
- None.

### 🟡 Addressed
- Non-zero-exit → structured error was test-covered (6.2) but not an explicit implementation step → 3.1 now names both error paths (timeout and non-zero exit with error kind + details).
- ADR-0002 (id allowlist) not cited in the config group that realizes it → group 2 header now cites ADR-0002.
- id→connection resolver implicit across 2.1/5.1 → accepted as-is (reviewer: no action needed).

### ✅ Verified
- All 21 tasks in trackable `- [ ] X.Y` format; dependency order sound (scaffolding → config → adapter → routing → orchestration → server → rig → docs/verification); all 12 spec requirements trace to tasks; all four ADRs honored; exact design values present (30s/5s, `\:` unescape, /31-/32 probe, strict CIDR, XDG order, NM-manages-only-Test-VPN); no invented scope; strict validate before archive included.