# Cleanup knowledge and native-tool guidance

## Goal

Space Scout should explain not only which paths are large, but what they represent,
whether they are safe to remove, and which platform-native tool should perform the
cleanup. The feature is advisory by default: scanning never executes a cleanup action.
Native execution stays gated to a later phase and is enabled adapter by adapter only
after the gates in "Acceptance and rollout gates" pass.

## User-facing model

Each entry may receive a `CleanupAdvice` record with:

- `category`: cache, build artifact, runtime, extension, application data, credential,
  or other structural category;
- `risk`: safe, review, or protected (see "Risk model and policy merge");
- `assessment_status`: assessed, rule_missing, adapter_missing, policy_error, or
  permission_denied;
- `reclaimable`: whether the bytes can normally be regenerated;
- `method`: native tool, move to Trash, or manual review; never empty for a scanned entry;
- `impact`: concise explanation of what the user may need to download or configure again;
- `reason`: the evidence that produced the advice.

The Explorer adds a Cleanup/Risk presentation tier when the terminal is wide enough.
The Inspector always shows the complete explanation, estimated reclaimable bytes, and
the exact target. Protected and uncertain entries never receive a bulk-clean action.

## Risk model and policy merge

### Policy is evaluated first

A single merge function combines the policy decision with the knowledge rule result.
Policy is evaluated first and dominates. Knowledge rules only assign `safe` or `review`
to paths whose policy status is `scan`. The merge function is the only place that turns
policy into risk.

| policy status | rule risk | assessment status | merged risk | cleanable |
| --- | --- | --- | --- | --- |
| excluded | any | any | protected | no |
| protected | any | any | protected | no |
| unlocked | any | any | review | no |
| scan | safe | assessed | safe | yes, preview plus explicit confirmation |
| scan | review | assessed | review | yes, explicit confirmation |
| scan | protected | assessed | protected | no |
| scan | any | rule_missing | review | yes, explicit confirmation |
| scan | any | adapter_missing | review | yes, explicit confirmation |
| scan | any | policy_error | protected | no |
| scan | any | permission_denied | protected | no |

`assessment_status` is meaningful only when the policy status is `scan`; otherwise the
policy decision is the assessment.

### Enum split

`risk` is one of `safe`, `review`, `protected`. `assessment_status` is one of `assessed`,
`rule_missing`, `adapter_missing`, `policy_error`, `permission_denied`. The overloaded
`unavailable` value is removed; each failure mode now has its own status.

### The safe predicate

A path is `safe` only when all four conditions hold:

1. tool-managed cache: the tool owns the directory and regenerates it;
2. documented regeneration path: the tool has a documented command or behavior that
   rebuilds it;
3. contains no user-authored content;
4. not an active session: no running process holds it as its working set.

### Confirmation policy

- safe actions: preview plus explicit confirmation;
- review actions: explicit confirmation;
- protected: no action.

### Advice generation passes policy

Advice generation computes the full affected path set for the proposed action. If any
path in that set is rejected by policy, the advice shows the policy reason only and
never shows the command.

## Architecture

Rules live in a local, public rule registry under `space_scout.knowledge`. A rule matches
normalized path components and platform, then returns data only; it never executes shell
text. Platform adapters implement a small typed interface for discovery, preview, and
execution. Adapters are optional and must report `adapter_missing` when their executable
is missing. This keeps the core scanner cross-platform and offline.

The knowledge layer plugs into existing integration points:

- classify labels (`space_scout.classify`): `system-protected`, `build-artifact`,
  `dependency-store`, `model-cache`, `cache`, `container-data`, `archive`, `installer`,
  `document`, `data`, `font`, `media`, `source`, `directory`, `extension`, `file`,
  `special`, `skipped`. Rules map these labels to advice categories.
- policy decisions (`space_scout.policy`): `PolicyDecision.status` in {scan, excluded,
  protected, unlocked}, produced by `decide()`, `safe_decide()`, and
  `cleanup_rejection()`. The merge function consumes `PolicyDecision`.
- `ReportRow` (`space_scout.output`): path, name, kind, logical_bytes, allocated_bytes,
  classification, status, reason. Advice attaches to rows; the `status` column keeps the
  policy status.
- column tiers (`space_scout.presentation`): `visible_columns(width)` returns narrow
  (name, on_disk), medium (adds status), or wide (adds logical, class). The Cleanup/Risk
  tier is a wide-tier column.
- Explorer and Inspector (`space_scout.tui`): `BrowseApp` renders `_entry_status` values
  TRASHED, UNLOCKED, SKIPPED, BLOCKED, WARNING, CLEANABLE; `action_trash` requires an
  exact-word confirmation through `_Prompt`; `action_unlock` is scan-only. The Inspector
  (`_show_details`) shows the exact target and will carry method, impact, and reason.
- config (`space_scout.config`): `Config(shortcuts, exclusions, overrides, sort_key,
  minimum_bytes)` with `load_config()` and `save_config()`. The adapter allowlist (see
  "Acceptance and rollout gates") is a persisted field here.

Initial adapters target uv, npm, Bun, Cargo, pyenv, rustup, Go environments, and common
editor caches. Runtime versions, editor extensions, credentials, active sessions, and
application databases are review/protected by default. Whole-directory deletion is not
used when a tool can remove one named version or cache through its own command; for
tools without a per-name command, the method matrix defines the operation (whole-cache
command where official, otherwise Trash or manual review).

## Target resolution and execution contract

### One resolver

A single resolver computes the target path and is shared by preview and execution. It
prefers asking the tool:

- uv: `uv cache dir`
- npm: `npm config get cache`
- Bun: `bun pm cache`
- Go: `go env GOCACHE GOMODCACHE`
- pyenv: `pyenv root`
- rustup: `rustup show home`
- Cargo: no query command exists; read `$CARGO_HOME`, else `~/.cargo`

If the resolver detects a redirect (the tool reports a location different from the
default, or an environment variable overrides it), it classifies the redirected target
or downgrades the advice to review with a `redirected` reason.

### Identity pinning

The resolver records `(st_dev, st_ino, st_mode)` via `lstat` for every target. Any
action re-verifies the identity immediately before acting and aborts on mismatch.

### TOCTOU

Before acting, the selected object is atomically renamed into a same-parent staging
directory. The resolver re-verifies identity and type after the rename, and only then
proceeds. A rename that fails or lands on a different object aborts the action.

### Freshness

Advice carries a TTL and a basis hash of the inputs that produced it. Execution
recomputes the target and invalidates the advice when the basis changed.

### Execution contract (phase-2 gate)

Native execution, when enabled, follows a fixed contract:

- absolute binary path with pinned version or hash;
- argv list with a `--` terminator;
- reject arguments that start with `-`, or contain newlines or control characters;
- fixed environment and working directory;
- timeout, cancellation, stdin from DEVNULL, captured stdout and stderr.

### Symlink semantics

One resolution semantics applies across matching, display, and execution. The UI
displays the link and the resolved target separately. Symlinks and mount points
escalate to review.

## Cleanup flow

1. Scan and classify without mutating the filesystem.
2. Merge policy with knowledge rules; show advice, evidence, risk, assessment status,
   and the recommended native command or Trash action.
3. Let the user select exact entries; never infer a wider target from a pattern.
4. Re-check policy, identity, existence, and size immediately before execution.
5. Safe actions require a preview plus explicit confirmation; review actions require
   explicit confirmation. When no native adapter exists, an eligible action can move the
   path to the system Trash.
6. Report the result and any follow-up impact. Trash remains recoverable; emptying Trash
   is outside Space Scout's scope.

## Trash semantics

### Two metrics

Track two distinct numbers: bytes moved out of the working set, and bytes actually
freed. Freed bytes come from a `statvfs` delta. Never call a path reclaimed before the
Trash is emptied.

### Cross-volume and no-Trash environments

When the target's `st_dev` differs from the Trash volume, refuse the move or keep the
object on the source volume. In environments without a Trash, report the method as `adapter_missing` or require a
stronger confirmation; never silently fall back to permanent deletion.

### Retention caveat

The operating system may auto-empty the Trash on a schedule. Emptying the Trash is an
explicit, guided action and is outside Space Scout's scope.

### Trash is not erasure

Trash moves data; it does not erase it. Credential-class paths never enter Trash flows.

## Toolchain method matrix

Authoritative facts. Commands are cited in "Sources".

### uv

- Per-name cleanup: `uv cache clean [PACKAGE]`; whole-cache maintenance: `uv cache prune`.
- Cache dir: `uv cache dir`; `$XDG_CACHE_HOME/uv` or `$HOME/.cache/uv` on macOS and Linux
  (uv follows XDG on macOS); `%LOCALAPPDATA%\uv\cache` on Windows.
- uv-managed Pythons (`~/.local/share/uv/python`) and tools (`~/.local/share/uv/tools`)
  are persistent data, not cache.
- Never modify the cache directory directly.

### npm

- Routine maintenance: `npm cache verify`.
- Whole-cache cleanup: `npm cache clean --force`; permanent; requires `--force`; no
  per-package option.
- Cache: `~/.npm` on Posix; `%LocalAppData%\npm-cache` on Windows.
- Credentials live in `~/.npmrc`, not in the cache.

### Bun

- Whole-cache cleanup: `bun pm cache rm`; permanent.
- Cache: `~/.bun/install/cache`.
- `~/.bun` also holds `bin/` and `install/global`, installed data; never bulk.

### Go

- `go clean -cache` clears GOCACHE; `go clean -modcache` clears GOMODCACHE. Both are
  whole-cache and permanent; no per-module option. Resolve paths via `go env`.

### Cargo

- No official cache-clean command exists. `cargo clean` removes only the project's
  `target/`.
- Registry cleanup is whole-directory and forces a full re-download.
- `$CARGO_HOME` also holds `bin/`, `.crates.toml` and `.crates2.json` (installed
  binaries), `config.toml`, and `credentials.toml`; all protected.

### pyenv

- No cache clean. `pyenv uninstall <version>` removes an installed runtime, not cache.
  Versions are runtimes, so review/protected.

### rustup

- No cache clean. `rustup toolchain uninstall <toolchain>` removes an installed
  toolchain, not cache.

### Editors

Only true cache directories are eligible. VS Code: `Cache`, `CachedData`, `GPUCache`,
`Code Cache`, `logs`. Extensions are handled via `code --uninstall-extension`, never by
deleting directories. `User/**` is protected. The implementation enumerates product by
OS by path. JetBrains `LocalHistory` is excluded and marked for verification during
implementation.

### Safe tier tightening

The safe tier means "locally regenerable without network". Cargo registry, Go modcache,
npm `_cacache`, and Bun cache become review with the reason "full re-download; unusable
offline".

### Parent-subtree rule

Bulk actions apply only when the entire subtree is safe. Explicit exclusion list:
`credentials.toml`, `config.toml`, `~/.bun/install/global`, `bin/`, pyenv `versions`,
rustup `toolchains`, VS Code `User/**`, JetBrains `LocalHistory`.

## Privacy and safety

The registry contains no usernames, absolute local paths, project names, credentials, or
telemetry. Advice is deterministic and explainable from local metadata. Unknown paths are
shown as `review` or a structural category; the knowledge layer never marks them
`safe` and never offers bulk cleaning. Existing
protected/excluded policy remains the final authority for all actions.

The registry boundary and the runtime display are separate. The registry stores only
normalized, relative patterns; it never stores a user's absolute paths. At runtime the
Inspector shows the exact target path so the user can verify what will be acted on.
Logs, reports, and shell history follow the same rule as the rest of Space Scout:
absolute paths can reveal usernames or local directory names, so review them before
sharing. Advice commands are shown only after policy passes (see "Advice generation
passes policy").

## Scope and priority

### Adapter ordering

Adapters are ordered by user base. "No adapter" entries are first-class: they carry an
actionable Trash path and a regeneration note, and `method` is never empty.

### Orphaned data

When a tool is uninstalled but its data remains, the entry gets the highest-value
guidance (what the data is, how to remove it, what is lost), not a silent `unavailable`.

### Local metric

Track "safe-tier bytes" and "known reclaimable bytes" as a visible local metric.

## Acceptance and rollout gates

### Outcome-based acceptance

An end-to-end test asserts scan, advice, action, and state change in sequence.
Disk-state assertions run after a Trash move. A local session summary reports moved
bytes versus freed bytes.

### Enablement checklist

Each adapter on each OS passes before it is enabled:

- CI installs the toolchain;
- real subprocess integration tests;
- preview target equals execute target assertion;
- coverage of timeout, partial failure, tool missing, and Trash fallback;
- Windows reports `adapter_missing` where the adapter is unsupported.

### Adapter allowlist

`Config` gains a persisted adapter allowlist, defaulting to read-only, as a kill switch.
A documented rollback path restores the default.

### v1 scope

v1 ships read-only advice plus the resolver for 2-3 tools. Native execution is enabled
adapter by adapter only after the gates above pass.

## Testing and rollout

Unit tests cover rule matching, platform selection, the merge truth table, and adapter
absence. The "risk precedence" tests are defined as tests of the merge truth table; they
exist only after the merge function exists. Scanner fixtures verify that advice does not
change measured bytes. CLI and TUI tests verify aligned columns, inspector explanations,
confirmation boundaries, and safe re-checks. The first release ships read-only advice
plus Trash previews; native execution is enabled adapter by adapter only after the gates
in "Acceptance and rollout gates" pass.

## Sources

- uv: docs.astral.sh/uv
- npm: docs.npmjs.com
- Bun: bun.sh/docs
- Cargo: doc.rust-lang.org
- rustup: rustup.rs
- pyenv: github.com/pyenv/pyenv
- Go: go.dev
- Editors: code.visualstudio.com (VS Code), jetbrains.com/help (JetBrains)