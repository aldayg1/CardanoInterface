@echo off
REM Change directory to the location of this script
cd /d "%~dp0"

REM Check if uv is installed
where uv >nul 2>&1 || (
    echo uv is not installed. Install it from https://docs.astral.sh/uv/getting-started/installation/ first.
    pause
    exit /b 1
)

REM uv run syncs the project environment from pyproject.toml + uv.lock
REM (equivalent to "uv sync") and then executes the app.
if exist CardanoInterface.py (
    uv run CardanoInterface.py
    pause
) else (
    echo Error: CardanoInterface.py not found.
    pause
)
