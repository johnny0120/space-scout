# Contributing

Thanks for helping improve Space Scout.

## Workflow

1. Sync the dev environment with `uv sync --extra dev`.
2. Run `uv run pytest -q` before opening a pull request.
3. Run `uv run ruff check .`, `uv run mypy src`, and `uv run python scripts/check_public_repo.py .` before opening a pull request.
4. Keep changes focused and covered by tests when behavior changes.
5. Avoid committing generated artifacts, local environment files, secrets, or personal machine paths.

## Expectations

- Keep the repository safe to publish.
- Prefer clear, small commits.
- Update documentation when commands or workflows change.
- Keep examples generic: use `$HOME` or `/path/to/project`, never a personal path.
