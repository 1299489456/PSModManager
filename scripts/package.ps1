[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$OutputRoot)
$ErrorActionPreference='Stop'
$taskProject=Split-Path -Parent $PSScriptRoot
$taskPython=Join-Path $taskProject '.venv/Scripts/python.exe'
if(-not (Test-Path -LiteralPath $taskPython -PathType Leaf)) { throw 'Existing .venv is required; no automatic installation.' }
Push-Location $taskProject
try {
    & $taskPython -I -B (Join-Path $taskProject 'pack_pyinstaller.py') --output-root $OutputRoot
    exit $LASTEXITCODE
} finally { Pop-Location }
