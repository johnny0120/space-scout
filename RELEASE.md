# Release checklist

1. Update `CHANGELOG.md` and the version in `pyproject.toml`.
2. Run `uv sync --locked --extra dev` and `uv run ./scripts/check.sh`.
3. Build and smoke-test native artifacts on every supported OS/architecture.
4. Create an annotated tag such as `v0.1.0` and push the tag.
5. Confirm the GitHub Release contains each artifact and its `.sha256` file.
6. Paste the release notes from `CHANGELOG.md` and call out platform limitations.

Never publish an artifact that has not passed the smoke test on its target operating system.
