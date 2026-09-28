#!/usr/bin/env bash
# Launcher for the testbuilder TUI (Hyper+U → Utilities panel).
# The lifecycle console runs in the REPO's venv.
set -euo pipefail
cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.."
exec uv run python -m testbuilder.tui "$@"
