#!/usr/bin/env bash
# Launcher for the testbuilder dashboard (Hyper+U → Utilities panel).
# The dashboard runs in the REPO's venv — it reads only repo state.
set -euo pipefail
cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.."
exec uv run python -m testbuilder.dashboard "$@"
