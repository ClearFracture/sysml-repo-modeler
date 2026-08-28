$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# Logging (override in the shell before running, or edit below).
if (-not $env:IMPORTER_LOG_LEVEL) { $env:IMPORTER_LOG_LEVEL = "INFO" }
if (-not $env:LANGFUSE_DEBUG) { $env:LANGFUSE_DEBUG = "false" }
# For Langfuse export troubleshooting, use DEBUG + true:
# $env:IMPORTER_LOG_LEVEL = "DEBUG"
# $env:LANGFUSE_DEBUG = "true"

if (-not (Test-Path ".venv")) {
    python -m venv .venv
}

& ".\.venv\Scripts\Activate.ps1"
python -m pip install -r requirements.txt

if (-not (Test-Path "ui\dist\index.html")) {
    Push-Location ui
    npm install
    npm run build
    Pop-Location
}

python -m importer
