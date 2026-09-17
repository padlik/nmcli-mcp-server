# AGENTS.md — OpenCode instructions for nmcli-mcp-server

## Repo state

- Bootstrap-stage repository: there is no source code yet. Tracked files are only this file, `CLAUDE.md`, `LICENSE`, and `openspec/` scaffolding (intent-driven schema, empty `specs/`, empty `changes/archive/`). Expect work to start from OpenSpec artifacts, not existing code.
- Product intent (from the repo name): an MCP server wrapping `nmcli` (Linux NetworkManager CLI). `nmcli` does not exist on macOS dev machines — runtime and integration verification will need a Linux host or container.
- `CLAUDE.md` only imports this file (`@AGENTS.md`). Keep guidance here; don't fork it there.
- `.claude/`, `.agents/`, and `.opencode/{agent,commands,skills}/` are gitignored local agent tooling (skills, `/opsx-*` commands) — present on this machine, but not part of the tracked repository.

## Skill loading (critical)

Before invoking ANY OpenSpec stage or `/opsx` command, load BOTH:

1. `openspec-workflow` — the standard workflow framework
2. The stage-specific skill (e.g. `openspec-propose`, `openspec-apply-change`, `openspec-verify-change`, `openspec-archive-change`, `openspec-explore`, `openspec-sync-specs`)

Load both when you see:

- `/opsx-apply`, `/opsx-propose`, `/opsx-verify`, `/opsx-archive`, `/opsx-explore`, `/opsx-sync`, `/opsx-new`, `/opsx-continue`, `/opsx-ff`
- "implement tasks from an OpenSpec change", "apply this change", "propose a change", etc.
- About to run `openspec instructions apply`, `openspec status`, `openspec list`, etc.
- Reading files under `openspec/changes/<name>/`

Skipping `openspec-workflow` loses critical workflow context.

## OpenSpec

- Workflow uses the `intent-driven` schema (`openspec/config.yaml`, `openspec/schemas/intent-driven/schema.yaml`): proposal → specs → design → adr → tasks → apply. `apply` tracks checkbox state in `tasks.md`; tasks must use `- [ ] X.Y <description>` format or they won't be tracked.
- Active stage rules from `openspec/config.yaml`: proposal must use the `grill-me` skill; design must use `c4-diagrams`; adr uses `architectural-decision-records`.
- Follow stages strictly in order. Do not skip, reorder, or implicitly advance a stage; move to the next step only on an explicit user command.
- Durable ADRs live at `<repo>/adr/` (top-level, outside `openspec/`), named `NNNN-kebab-title.md` with a repo-wide monotonic sequence. Accepted ADRs are immutable — to change a decision, write a NEW ADR whose Status is "accepted, supersedes ADR-NNNN" and whose `Supersedes:` field names the prior one. Never edit a prior ADR file.
- Before archive, run `openspec validate <change> --type change --strict`.
- For propose, apply, verify, and archive, follow the `openspec-git-discipline` skill exactly: proposal committed before apply, merge before archive, and never archive/merge without the required user approval and workflow state.

## Python and uv

- Use uv for all Python environment, dependency, execution, and testing operations: `uv sync`, `uv add <package>`, `uv add --dev <package>`, `uv run <command>`. Never use pip, `python -m venv`, Poetry, Pipenv, Conda, or manually managed virtual environments. Do not rely on an activated venv — use `uv run python ...` or the project's configured entry point. Use the Python version declared by the project; don't install dependencies globally.
- Python style: PEP 8; type hints on all function parameters and return types; docstrings on public modules/classes/functions; small cohesive modules; explicit error handling that preserves context (no silently swallowed exceptions); no new dependencies when stdlib suffices; preserve backward compatibility unless the change requires breaking it.

## Verification

- After every code change, run:
  - `ruff check .`
  - `vulture . vulture_whitelist.py --min-confidence 80`
- Run the most relevant tests after each meaningful implementation step; run the full applicable suite and all static checks before declaring a task complete.
- If a check fails: find and fix the root cause, re-run it, and don't declare completion while it still fails.
- Never weaken, disable, suppress, or modify lint/test rules to make verification pass unless explicitly requested.
- Add tests for new behavior unless there is a documented reason not to.

## Git

- Never work directly on `main`. Inspect current Git state before modifying anything.
- Single-agent isolated change → dedicated `feature/*` or `fix/*` branch.
- Multi-agent or parallel work → one Git worktree per independent task; never share a working tree between agents. Prefer worktrees whenever change scope is uncertain — apparently independent changes can collide in the same code, tests, fixtures, or config.
- No commits, merges, history rewrites, or force-pushes without explicit user approval or command. Keep commits focused; inspect the diff before committing and exclude unrelated changes.

## General

- Retrieval-led reasoning: inspect the codebase, config, docs, and skills before assuming; verify unfamiliar APIs against authoritative sources; don't invent APIs, commands, files, or conventions. Prefer the smallest correct change; avoid unrelated refactoring.
- Don't install tools/packages/dependencies or modify global system configuration without explicit approval; no destructive operations without authorization.
- When a change alters user-visible behavior, update the relevant README/docs and configuration/usage examples as part of the work. Docs must describe actual implemented behavior, not intentions.
