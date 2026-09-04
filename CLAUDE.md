# Project Instructions for AI Agents

This file provides instructions and context for AI coding agents working on this project.

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:6cd5cc61 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.

## Agent Context Profiles

The managed Beads block is task-tracking guidance, not permission to override repository, user, or orchestrator instructions.

- **Conservative (default)**: Use `bd` for task tracking. Do not run git commits, git pushes, or Dolt remote sync unless explicitly asked. At handoff, report changed files, validation, and suggested next commands.
- **Minimal**: Keep tool instruction files as pointers to `bd prime`; use the same conservative git policy unless active instructions say otherwise.
- **Team-maintainer**: Only when the repository explicitly opts in, agents may close beads, run quality gates, commit, and push as part of session close. A current "do not commit" or "do not push" instruction still wins.

## Session Completion

This protocol applies when ending a Beads implementation workflow. It is subordinate to explicit user, repository, and orchestrator instructions.

1. **File issues for remaining work** - Create beads for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **Handle git/sync by active profile**:
   ```bash
   # Conservative/minimal/default: report status and proposed commands; wait for approval.
   git status

   # Team-maintainer opt-in only, unless current instructions forbid it:
   git pull --rebase
   git push
   git status
   ```
5. **Hand off** - Summarize changes, validation, issue status, and any blocked sync/commit/push step

**Critical rules:**
- Explicit user or orchestrator instructions override this Beads block.
- Do not commit or push without clear authority from the active profile or the current user request.
- If a required sync or push is blocked, stop and report the exact command and error.
<!-- END BEADS INTEGRATION -->



## Project Context

**Read `docs/ARCHITECTURE.md` first in any fresh session** — it is the
authoritative current state, including every verified deviation from the
original spec. `docs/FORGE_API.md` is the verified API contract;
`docs/HANDOFF.md` is the original (German, partially superseded) spec.

sptmod: a git-based mod manager for Single Player Tarkov. Python core +
typer CLI + Tauri v2 desktop app (RPC) + Svelte 5 frontend (stage 1); stage 2
will port core+CLI to Rust — the pytest suite is the specification for that
port.

## Build & Test

```bash
uv sync                              # Python env
uv run pytest                        # full suite (~1,180 tests)
uv run pytest --cov                  # with coverage gates (line & branch 100%)
uv run ruff check src
uv run mypy --strict src
cd frontend && pnpm install && pnpm check && pnpm test && pnpm build
cd frontend && pnpm vitest run --coverage  # frontend coverage (v8, 100%)
cargo build && cargo test            # Rust (Tauri, src-tauri/)
cargo llvm-cov --package sptmod-desktop --lib --tests --summary-only  # Rust coverage (91%)

# after changing service functions or request/response schemas:
uv run python -m sptmod.server.dump_openapi openapi.json
cd frontend && pnpm gen:api          # regenerates src/lib/api/types.ts

# run the app (Tauri desktop):
uv tool install --editable .         # install sptmod CLI
cargo tauri dev                      # hot reload during development
```

## Architecture in five lines

- `src/sptmod/core/` is pure logic: typed exceptions, dataclasses, injected
  progress callbacks; it knows neither CLI nor HTTP.
- `subprocess` ONLY in `core/repo.py`; `httpx` ONLY in `core/forge/client.py`.
- One contract, four transports: service function ↔ `sptmod rpc <name>` ↔
  Tauri `#[tauri::command] rpc()` ↔ client.ts wrapper — guarded by
  route-per-function and schema-parity tests.
- `frontend/src/lib/api/transport.ts` is the only invoke/listen site;
  `types.ts` is generated, never hand-edited.
- Git never surfaces in user-facing language (auto-snapshot handles dirty
  profiles; errors speak plain language).

## Conventions & hard-won rules

- Tests are the Rust-port spec: black-box, behavior-focused; don't weaken
  them to make an implementation fit.
- The Forge moved to `https://sp-mod.com/api/v0` (no auth exists; env
  override `SPTMOD_FORGE_URL`). Never hit the real API from tests (respx).
- Game layouts: `SPT_Runtime/` (4.1+) and `SPT/` (4.0) via
  `game_dir.detect_layout`; never hardcode `SPT/` paths.
- Mod identity: the install-commit `Mod:` trailer (from the mod's own
  package.json) ≠ the Forge display name; link rows by Forge id.
- Catalog cache is disposable; schema evolves via the ALTER list in
  `catalog._MOD_COLUMN_MIGRATIONS`.
- Line length 88 (ruff E501) — including docstrings.
- Real-mod corpus for manual verification lives untracked in `_data/mods/`.
- Tests exercise only public entry points and observable effects; never patch
  or call private names (service functions, public methods, CliRunner, `sptmod rpc`, TS exports).
- Only mocked boundaries: HTTP (respx), subprocess (stub binary), environment
  (PATH, HOME, XDG_*), filesystem conditions. Public keyword-only seams for
  testability: `locate_directory(roots=)`, `game_lock(timeout=, poll_interval=)`.
- Sibling cases parametrized with descriptive ids; coverage exclusions only via
  the two `exclude_also` patterns in pyproject (sys.platform, TYPE_CHECKING).
  No `# pragma: no cover`; unreachable defensive code is deleted.
- A strict `xfail` may pin a known production bug only with a bead reference
  (currently: spt-mod-manager-6o4).
