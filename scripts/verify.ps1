param(
    [Parameter(Mandatory=$true)][string]$Task,
    [Parameter(Mandatory=$true)][string]$Profile,
    [string]$Authorization
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$python = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    # This entry point must retain evidence even when Python cannot start.
    # All locations are derived here; callers cannot supply an output directory.
    function Assert-EvidencePath([string]$Path) {
        $fullPath = [IO.Path]::GetFullPath($Path)
        $projectFull = [IO.Path]::GetFullPath($projectRoot)
        if ($fullPath -ne $projectFull -and -not $fullPath.StartsWith($projectFull + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Evidence path escaped the project root.'
        }
        $candidate = $fullPath
        while ($candidate) {
            if (Test-Path -LiteralPath $candidate) {
                $item = Get-Item -LiteralPath $candidate -Force
                if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                    throw 'Evidence path contains a reparse point.'
                }
            }
            $candidate = [IO.Path]::GetDirectoryName($candidate)
        }
    }
    function Write-EvidenceText([string]$Name, [string]$Value) {
        $destination = Join-Path $run $Name
        Assert-EvidencePath $destination
        [IO.File]::WriteAllText($destination, $Value, [Text.UTF8Encoding]::new($false))
    }
    $reason = 'Missing dependency: existing .venv/Scripts/python.exe required; no dependencies were installed; no command started.'
    try {
        $started = [DateTime]::UtcNow.ToString('o')
        $safeTask = if ($Task -cmatch '^PS-[0-9]{2}-[0-9]{2}$') { $Task } else { 'invalid-task' }
        $runId = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-missing-python-' + [Guid]::NewGuid().ToString('N').Substring(0, 8)
        $run = Join-Path $projectRoot "docs/development/evidence/$safeTask/$runId"
        Assert-EvidencePath $run
        if (Test-Path -LiteralPath $run) { throw 'Fresh evidence directory already exists.' }
        [IO.Directory]::CreateDirectory($run) | Out-Null
        Assert-EvidencePath $run
        $entryCommand = @('pwsh', '-NoProfile', '-File', $PSCommandPath, '-Task', $Task, '-Profile', $Profile)
        if ($Authorization) { $entryCommand += @('-Authorization', $Authorization) }
        Write-EvidenceText 'command.txt' (($entryCommand | ConvertTo-Json -Compress) + "`ncwd=$projectRoot`nNo child command started; runner exit code: 2`n")
        Write-EvidenceText 'stdout.log' ''
        Write-EvidenceText 'stderr.log' ($reason + "`n")
        $environment = [ordered]@{
            schema_version = 1
            os = [Environment]::OSVersion.VersionString
            architecture = [Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
            powershell = $PSVersionTable.PSVersion.ToString()
            python = $null
            git = 'NOT_EVALUATED: Python missing'
            code_fingerprint = (Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant()
            dependencies = @{ python = 'MISSING: .venv/Scripts/python.exe' }
            isolation_root = $run
            constraints = 'No dependency install, product process, real game, save, or network action was attempted.'
        }
        Write-EvidenceText 'environment.json' ($environment | ConvertTo-Json -Depth 8)
        Write-EvidenceText 'cases.json' (@{ schema_version = 1; cases = @(); reason = 'Route and required cases could not be evaluated because Python is missing.' } | ConvertTo-Json -Depth 8)
        $inputLines = foreach ($inputName in @('scripts/verify.ps1', 'docs/development/verification-profiles.json', 'docs/development/verification-summary.schema.json')) {
            $inputPath = Join-Path $projectRoot $inputName
            Assert-EvidencePath $inputPath
            if (Test-Path -LiteralPath $inputPath -PathType Leaf) {
                (Get-FileHash -LiteralPath $inputPath -Algorithm SHA256).Hash.ToLowerInvariant() + '  ' + $inputName
            }
        }
        Write-EvidenceText 'inputs.sha256' (($inputLines -join "`n") + "`n")
        $summary = [ordered]@{
            schema_version = 1; task_id = $Task; profile = $Profile; run_id = $runId
            verification_status = 'BLOCKED'; started_at = $started; ended_at = [DateTime]::UtcNow.ToString('o')
            command = $entryCommand; exit_code = $null; runner_exit_code = 2
            reference_commit = 'a7e875112a175a914bb4706a82589d96d62adc70'; git_commit = $null
            counts = @{ discovered = 0; executed = 0; passed = 0; failed = 0; skipped = 0 }
            required_cases = @(); blockers = @($reason)
            gaps = @('Route, required cases and product behavior were not evaluated; Python did not start.')
            evidence = @('environment.json', 'cases.json', 'command.txt', 'stdout.log', 'stderr.log', 'inputs.sha256', 'artifacts.sha256')
            commands = @()
        }
        Write-EvidenceText 'summary.json' ($summary | ConvertTo-Json -Depth 8)
        $artifactLines = foreach ($artifact in (Get-ChildItem -LiteralPath $run -File | Sort-Object Name)) {
            Assert-EvidencePath $artifact.FullName
            (Get-FileHash -LiteralPath $artifact.FullName -Algorithm SHA256).Hash.ToLowerInvariant() + '  ' + $artifact.Name
        }
        Write-EvidenceText 'artifacts.sha256' (($artifactLines -join "`n") + "`n")
        [Console]::Error.WriteLine("BLOCKED: $reason Evidence: $run")
    }
    catch {
        [Console]::Error.WriteLine("BLOCKED: $reason Evidence could not be safely recorded: $($_.Exception.Message)")
    }
    exit 2
}
$arguments = @('-B', '-m', 'scripts.verification.runner', '--task', $Task, '--profile', $Profile)
if ($Authorization) { $arguments += @('--authorization', $Authorization) }
Push-Location $projectRoot
try { & $python @arguments; $result = $LASTEXITCODE } finally { Pop-Location }
exit $result
