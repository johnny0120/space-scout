# Cleanup knowledge and native-tool guidance

## Goal

Space Scout should explain not only which paths are large, but what they represent,
whether they are safe to remove, and which platform-native tool should perform the
cleanup. The feature is advisory by default: scanning never executes a cleanup action.

## User-facing model

Each entry may receive a `CleanupAdvice` record with:

- `category`: cache, build artifact, runtime, extension, application data, credential,
  or other structural category;
- `risk`: safe, review, protected, or unavailable;
- `reclaimable`: whether the bytes can normally be regenerated;
- `method`: native tool, move to Trash, or manual review;
- `impact`: concise explanation of what the user may need to download or configure again;
- `reason`: the evidence that produced the advice.

The Explorer adds a Cleanup/Risk presentation tier when the terminal is wide enough.
The Inspector always shows the complete explanation, estimated reclaimable bytes, and
the exact target. Protected and uncertain entries never receive a bulk-clean action.

## Architecture

Rules live in a local, public rule registry under `space_scout.knowledge`. A rule matches
normalized path components and platform, then returns data only; it never executes shell
text. Platform adapters implement a small typed interface for discovery, preview, and
execution. Adapters are optional and must report `unavailable` when their executable is
missing. This keeps the core scanner cross-platform and offline.

Initial adapters target uv, npm, Bun, Cargo, pyenv, rustup, Go environments, and common
editor caches. Runtime versions, editor extensions, credentials, active sessions, and
application databases are review/protected by default. Whole-directory deletion is not
used when a tool can remove one named version or cache through its own command.

## Cleanup flow

1. Scan and classify without mutating the filesystem.
2. Show advice, evidence, risk, and the recommended native command or Trash action.
3. Let the user select exact entries; never infer a wider target from a pattern.
4. Re-check policy, existence, and size immediately before execution.
5. Require an explicit confirmation for every review action; safe cache actions still
   use a preview and can be moved to the system Trash when no native adapter exists.
6. Report the result and any follow-up impact. Trash remains recoverable; emptying Trash
   is outside Space Scout's scope.

## Privacy and safety

The registry contains no usernames, absolute local paths, project names, credentials, or
telemetry. Advice is deterministic and explainable from local metadata. Unknown paths are
shown as `review` or a structural category, never as permission to delete. Existing
protected/excluded policy remains the final authority for all actions.

## Testing and rollout

Unit tests cover rule matching, platform selection, risk precedence, and adapter absence.
Scanner fixtures verify that advice does not change measured bytes. CLI and TUI tests
verify aligned columns, inspector explanations, confirmation boundaries, and safe
re-checks. The first release ships read-only advice plus Trash previews; user-confirmed
native execution is enabled adapter by adapter after platform-specific tests pass.
