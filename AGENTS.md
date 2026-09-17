# AGENTS.md — OpenCode Instructions for smallpieclaw

## CRITICAL: Skill loading

**Before invoking ANY OpenSpec stage or /opsx command, you MUST load TWO skills:**

1. Load `openspec-workflow` — the standard workflow framework
2. Load the stage-specific skill (e.g. `openspec-apply-change`, `openspec-propose`, etc.)

**Concrete triggers — load both skills when you see:**
- User says `/opsx-apply`, `/opsx-propose`, `/opsx-verify`, `/opsx-archive`, `/opsx-explore`, `/opsx-sync`
- User says "implement tasks from an OpenSpec change", "apply this change", "propose a change", etc.
- You are about to run `openspec instructions apply`, `openspec status`, `openspec list`, etc.
- You are reading files under `openspec/changes/<name>/`

Failure to load `openspec-workflow` will result in missing critical workflow context.

## Reasoning

- Prefer retrieval-led reasoning over relying on pretrained knowledge.
- Inspect the existing codebase, configuration, documentation, tests, and relevant skills before making assumptions.
- Reuse existing patterns, utilities, abstractions, and conventions unless there is a concrete reason to introduce something new.
- For unfamiliar APIs, libraries, frameworks, or project-specific behavior, verify against authoritative documentation or the repository before implementing.
- Distinguish clearly between facts verified from the repository, facts retrieved from external documentation, and assumptions.
- Do not invent APIs, configuration options, files, commands, or project conventions.
- Before implementing a non-trivial change, identify affected components, dependencies, interfaces, tests, documentation, and configuration.
- Prefer the smallest correct change that satisfies the requirement. Avoid unrelated refactoring.



## Code quality

- Follow the existing project architecture and conventions.
- Avoid unnecessary superclasses, deep inheritance hierarchies, and large files.
- Prefer small, cohesive modules and functions with clear responsibilities.
- Follow PEP 8 and the project’s established formatting/linting conventions.
- Include type hints for all function parameters and return types.
- Write docstrings for all public modules, classes, functions, and methods.
- Prefer explicit, readable code over clever or overly abstract implementations.
- Avoid premature abstractions. Introduce an abstraction only when it provides a clear maintainability or reuse benefit.
- Preserve backward compatibility unless the requested change explicitly requires breaking it.
- Handle errors explicitly and preserve useful error context.
- Do not silently swallow exceptions or introduce broad exception handling without justification.
- Keep configuration, secrets, environment-specific values, and business logic properly separated.
- Do not introduce dependencies when the existing standard library or project dependencies are sufficient.


## Verification

- After every code change, run:
    - ruff check .
    - vulture . vulture_whitelist.py --min-confidence 80
- Run the most relevant tests after each meaningful implementation step.
- Before declaring a task complete, run the complete applicable test suite and all required static checks.
- If a check fails:
    1. Investigate the root cause.
    2. Fix the issue.
    3. Re-run the failed check.
    4. Do not declare the task complete while required checks remain failing.
- Do not weaken, disable, suppress, or modify lint/test rules merely to make verification pass unless explicitly requested.
- When tests are missing for new behavior, add appropriate tests unless there is a documented reason not to.
- Verify both the changed behavior and relevant regression scenarios.


## Development discipline 

- Never work directly on the main branch.
-  Before modifying code, inspect the current Git state, branch, and any existing changes.
- For a single-agent, isolated modification, use a dedicated feature/* or fix/* branch.
- For multi-agent or parallel development, use a separate Git worktree/workspace for each independent task. Never allow parallel agents to modify the same working tree.
- Prefer worktrees whenever the scope or impact of parallel changes is uncertain. Changes that appear independent may affect the same code, tests, fixtures, configuration, or integration points and can therefore cause conflicts.
- Keep each worktree focused on one well-defined task and avoid sharing uncommitted changes between worktrees.
- Delegate independent, well-defined tasks to sub-agents when this improves correctness or efficiency.
- When delegating work, clearly define the task scope, constraints, and expected output.
- Review sub-agent results before incorporating them into the primary implementation.
- Before merging parallel work, run the relevant tests and resolve any conflicts or behavioural interactions between the changes.

## Git

- Do not commit or merge changes without explicit user approval or a direct command.
- Do not rewrite Git history unless explicitly instructed.
- Do not force-push unless explicitly instructed.
- Keep commits focused when the user has requested commits.
- Do not include unrelated changes in the requested work.
- Before committing, inspect the diff and verify that only intended changes are included.

## OpenSpec

- When using OpenSpec, follow its defined workflow strictly and in order.
- Do not skip, reorder, or implicitly advance OpenSpec steps.
- Do not move to the next OpenSpec step without an explicit user command.
- For propose, apply, verify, and archive workflows, use the local openspec-git-discipline skill.
- Follow the skill’s proposal-commit-before-apply and merge-before-archive requirements exactly.
- Do not perform OpenSpec archive/merge operations without the required user approval and workflow state.

## Tools and environment

- Do not install tools, packages, programs, scripts, plugins, or dependencies without explicit user approval or a direct command.
- Do not modify global system configuration without explicit approval.
- Do not mount filesystems without explicit user approval.
- Do not make destructive operations unless explicitly authorized.

## Documentation

- When a feature changes user-visible behavior, update the relevant README/documentation as part of the implementation.
- Update configuration examples when configuration changes.
- Update usage examples when CLI/API behavior changes.
- Update architecture/developer documentation when the implementation changes an architectural contract.
- Do not add documentation that merely repeats obvious implementation details.
- Documentation must describe the actual implemented behavior, not intended or hypothetical behavior.

## Usability

- Consider usability for every user-facing change.
-  Check error messages, CLI/API ergonomics, defaults, discoverability, and failure behavior.
-  Prefer actionable error messages that explain what went wrong and, where possible, how to fix it.
-  Preserve existing user workflows unless a change intentionally modifies them.
-  When adding a feature, consider:
    - configuration ergonomics;
    - sensible defaults;
    - backward compatibility;
    - clear error handling;
    - help/usage output;
    - examples;
    - logging/observability;
    - migration requirements;
    - accessibility where applicable.
- Do not add unnecessary UX complexity merely to expose internal implementation details.

