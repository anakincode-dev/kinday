#!/usr/bin/env bash
# PostToolUse hook: after Edit/Write on a .py file, run ruff format + ruff check --fix on it.
set -uo pipefail

file="$(python3 -c 'import json, sys; print(json.load(sys.stdin).get("tool_input", {}).get("file_path", ""))')"

case "$file" in
  *.py)
    export PATH="$HOME/.local/bin:$PATH"
    uv run ruff format "$file" 2>&1
    uv run ruff check --fix "$file" 2>&1
    ;;
esac

exit 0
