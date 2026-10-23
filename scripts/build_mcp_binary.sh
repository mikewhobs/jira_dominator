#!/usr/bin/env bash
# Build a one-file MCP executable with an isolated CPython 3.12 venv.
# Build with the same CPython as .tool-versions so release binaries match local runs.
# stdout of the finished binary must stay clean: the MCP session is stdio.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VENV="$ROOT/build/venv"

py_bin() {
  if [[ -x "$VENV/bin/python" ]]; then
    echo "$VENV/bin/python"
  elif [[ -x "$VENV/Scripts/python.exe" ]]; then
    echo "$VENV/Scripts/python.exe"
  else
    return 1
  fi
}

venv_ok() {
  local py
  py="$(py_bin)" || return 1
  "$py" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)'
}

base_python() {
  if command -v mise >/dev/null 2>&1; then
    mise install python@3.12.13
    mise exec python@3.12.13 -- python -c 'import sys; print(sys.executable)'
    return
  fi
  local candidate
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 \
      && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)'; then
      command -v "$candidate"
      return
    fi
  done
  echo "Python 3.12 is required to build. That is the version in .tool-versions." >&2
  return 1
}

if ! venv_ok; then
  rm -rf "$VENV"
  base="$(base_python)"
  echo "Creating build venv with $base" >&2
  "$base" -m venv "$VENV"
  venv_ok
fi

PY="$(py_bin)"
echo "Building with $PY" >&2
"$PY" -m pip install -r requirements.txt pyinstaller
"$PY" -m PyInstaller --noconfirm --clean --onefile \
  --name jira-dominator \
  --distpath dist \
  --workpath build/pyinstaller \
  --specpath build \
  --collect-submodules mcp \
  --collect-submodules jira \
  --hidden-import dotenv \
  --hidden-import markdown \
  jira_mcp_server.py
