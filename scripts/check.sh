#!/usr/bin/env bash
set -u
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

if [[ -t 1 ]]; then
  RED=$'\033[31m'
  GREEN=$'\033[32m'
  RESET=$'\033[0m'
else
  RED=""
  GREEN=""
  RESET=""
fi

failed=0

run_check() {
  local label="$1"
  shift
  printf '%s\n' "==> $label"
  if uv run "$@"; then
    printf '%s\n' "${GREEN}PASS${RESET} $label"
  else
    printf '%s\n' "${RED}FAIL${RESET} $label"
    failed=1
  fi
  printf '\n'
}

printf '%s\n' "Space Scout quality gate"
printf '%s\n' "repo: $REPO_ROOT"
printf '\n'

run_check "ruff" ruff check src tests scripts setup.py
run_check "mypy" mypy src tests scripts
run_check "credentials" python scripts/check_public_repo.py .
run_check "pytest" pytest -q

if [[ $failed -eq 0 ]]; then
  printf '%s\n' "${GREEN}All checks passed${RESET}"
else
  printf '%s\n' "${RED}One or more checks failed${RESET}"
fi

exit "$failed"
