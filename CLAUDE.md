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
`docs/HANDOFF.md` is the product specification (rewritten in English from
the original handoff; its History section lists what changed).

sptmod: a git-based mod manager for Single Player Tarkov. Python core +
typer CLI + Tauri v2 desktop app (RPC) + Svelte 5 frontend (stage 1); stage 2
will port core+CLI to Rust — the pytest suite is the specification for that
port.

## Build & Test

```bash
uv sync                              # Python env
uv run pytest                        # full suite
uv run pytest --cov                  # with coverage gates (line & branch 100%)
uv run ruff check src
uv run pyright                       # strict type checking on src + tests
uv run pylint src tests              # strict linting (design limits to observed maxima)
uv run mypy --strict src tests       # strict typing on both src and tests
cd frontend && pnpm install && pnpm check && pnpm test && pnpm build
cd frontend && pnpm vitest run --coverage  # src/lib coverage (v8, gated at 100%)
cd frontend && pnpm test:components        # Svelte components/routes
cd frontend && pnpm test:components --coverage  # its own thresholds: a measured floor, not 100%
cargo build && cargo test            # Rust (Tauri, src-tauri/)
cd src-tauri && cargo llvm-cov       # Rust coverage — reported only, no enforced floor

# after changing service functions or request/response schemas:
uv run python -m sptmod.server.dump_openapi openapi.json
cd frontend && pnpm gen:api          # regenerates src/lib/api/types.ts
uv run pytest tests/test_dump_openapi.py  # verify contract drift detection

# run the app (Tauri desktop):
uv tool install --editable .         # install sptmod CLI
cargo tauri dev                      # hot reload during development
```

Numbers that no gate enforces (test counts, Rust coverage percentages) are
deliberately absent here — measure them when you need them; a hardcoded
snapshot only rots. Where a gate does enforce a figure (Python coverage 100%,
`src/lib` coverage 100%, the pylint design limits) it is named.

## Architecture in five lines

- `src/sptmod/core/` is pure logic: typed exceptions, dataclasses, injected
  progress callbacks; it knows neither CLI nor HTTP.
- `subprocess` ONLY in `core/repo.py` and `core/launch/`; `httpx` ONLY in `core/forge/client.py` (Forge),
  `core/sptserver.py` (local server) and `core/sptrelease/client.py` (SPT release source) — one per remote service.
- One contract, four transports: service function ↔ `sptmod rpc <name>` ↔
  Tauri `#[tauri::command] rpc()` ↔ client.ts wrapper — guarded by
  route-per-function and schema-parity tests.
- `frontend/src/lib/api/transport.ts` is the only invoke/listen site;
  `types.ts` is generated, never hand-edited.
- Git never surfaces in user-facing language (auto-snapshot handles dirty
  profiles; errors speak plain language).

## Package layouts

Three former single files are packages now; import from the package, not from
a module path that no longer exists:

- `src/sptmod/cli/` — `base.py` (the typer `app`, shared helpers, `add_typer`),
  `mods.py`, `repo.py`, `sources.py`, `spt.py`, `gamedir.py`, `reports.py`,
  `rpc.py`. `__init__.py` imports every module for its side effect of
  registering commands, so the import order there fixes the `sptmod --help`
  command order (pinned by `tests/test_smoke.py`).
- `src/sptmod/core/forge/catalog/` — `schema.py` (DDL, the migration list, the
  default db path), `rows.py` (row ↔ dataclass mapping), `store.py` (the
  `CatalogStore` itself). The package no longer re-exports `platformdirs`;
  tests patch `XDG_CONFIG_HOME`.
- `src/sptmod/server/schemas/` — `base.py`, `mods.py`, `requests.py`,
  `modsets.py`, `runtime.py`, with `__init__.py` as the facade that re-exports
  the contract names unchanged.

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
  `sptmod.core.forge.catalog.schema.MOD_COLUMN_MIGRATIONS`.
- Line length 119 (ruff E501) — including docstrings.
- Real-mod corpus for manual verification lives untracked in `_data/mods/`.
- Tests exercise only public entry points and observable effects; never patch
  or call private names (service functions, public methods, CliRunner, `sptmod rpc`, TS exports).
- Only mocked boundaries: HTTP (respx), subprocess (stub binary), environment
  (PATH, HOME, XDG_*), filesystem conditions. Public keyword-only seams for
  testability: `locate_directory(roots=)`, `game_lock(timeout=, poll_interval=)`,
  and `stop_game(terminate_timeout=)` / `watch_game(terminate_timeout=)` — the
  seconds a process gets to close on its own before it is killed.
- Two frontend suites with different standards: `pnpm test` gates `src/lib` at
  100%, `pnpm test:components` runs `frontend/tests/**` under happy-dom against a
  measured coverage floor. Component tests mock `src/lib/api/transport.ts` and
  nothing else, so they pin the real RPC names and payloads; happy-dom over jsdom
  because shipped components call `matchMedia` and `scrollIntoView`.
- Sibling cases parametrized with descriptive ids; coverage exclusions only via
  the two `exclude_also` patterns in pyproject (sys.platform, TYPE_CHECKING).
  NO suppression pragmas of any kind in src or tests (`type: ignore`, `noqa`,
  `pylint: disable`, `pragma: no cover`) — lint findings are resolved by
  refactoring; the only configured knobs are pylint design limits set to the
  exact observed maxima — among them `max-module-lines = 900`, set by
  `tests/test_services_update.py`, the longest module in the tree — plus
  `min-similarity-lines = 24` for the CatalogMod dataclass/schema mirror, and
  `[tool.pylint.imports] allow-any-import-level = ["libarchive"]`, because
  libarchive-c loads `libarchive.so` at import time, so archive.py imports it
  inside the 7z fallback and that knob is the pragma-free way to allow it.
- Tests assert behavior through public entry points, never the shape of the source: no pinned counts (functions,
  error classes, routes, options, module lines), no "X was removed / is absent from the tree" assertions, no
  restating a constant against itself. Reflection-derived contract tests stay — they enumerate a live list, so a new
  entry is covered automatically, but they carry no hardcoded length. Packaging and release invariants (version
  agreement across manifests and the like) live in `.github/workflows/release.yml`, not in the suite.
- Every test module, class, and function has a docstring stating setup,
  assertion, and reason. Tests are fully typed via fixture Protocols in
  `tests/fixture_types.py`; shared builders in `tests/conftest.py`,
  `tests/forge_json.py`, `tests/github_json.py`, `tests/pe_builder.py`,
  `tests/launch_helpers.py`, `tests/forge_install_env.py` and
  `tests/profile_cleanup_corpus.py`; apart from `conftest.py` those are plain
  imports, not pytest plugins.
- Area fixture modules `tests/fixtures_forge.py`, `tests/fixtures_launch.py`,
  `tests/fixtures_profile.py`, `tests/fixtures_rpc.py` and `tests/fixtures_spt.py`
  are registered via `pytest_plugins` in `tests/conftest.py`, so their fixtures
  are suite-wide.
- `conftest.py`'s process-scan guard ignores processes belonging to a test run,
  so a second pytest run of this repo no longer aborts the first.
- A strict `xfail` may pin a known production bug only with a bead reference.
