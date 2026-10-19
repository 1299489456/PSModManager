[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$OutputRoot)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    [Console]::Error.WriteLine('Missing dependency: project .venv Python; no installation attempted.')
    exit 2
}
& $python -I -B (Join-Path $PSScriptRoot 'build_fixtures.py') --output-root $OutputRoot
exit $LASTEXITCODE
