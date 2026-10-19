[CmdletBinding()]
param(
    [string]$DocsRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$ReportPath,
    [ValidateSet('None', 'DependencyCycle', 'MissingLocalLink', 'UnprovenVerification')]
    [string]$Probe = 'None'
)

# Documentation checks only. Never invokes application code or changes product status.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$issues = [System.Collections.Generic.List[string]]::new()
$checks = 0
function Test-Condition([bool]$Condition, [string]$Message) {
    $script:checks++
    if (-not $Condition) { $script:issues.Add($Message) }
}
function Read-Doc([string]$RelativePath) {
    $path = Join-Path $script:resolvedDocs $RelativePath
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        $script:issues.Add("Missing document: $RelativePath")
        return ''
    }
    return [IO.File]::ReadAllText($path)
}

try {
    $resolvedDocs = (Resolve-Path -LiteralPath $DocsRoot).Path
    $ledger = (Read-Doc 'task-register.json') | ConvertFrom-Json -Depth 80
    $tasks = @($ledger.tasks)
    # Optional negative probes mutate only the in-memory view, never source docs.
    if ($Probe -eq 'DependencyCycle' -and $tasks.Count -gt 1) { $tasks[0].depends_on = @($tasks[1].id) }
    if ($Probe -eq 'UnprovenVerification' -and $tasks.Count -gt 0) {
        $tasks[0].status = 'VERIFIED'
        $tasks[0].verification = 'PASS'
        $tasks[0].evidence = @()
    }
    $taskMap = @{}
    $orderMap = @{}
    $allowedStatus = @('TODO', 'IN_PROGRESS', 'IMPLEMENTED', 'VERIFIED', 'BLOCKED', 'DEFERRED')
    $allowedVerification = @('NOT_RUN', 'PASS', 'FAIL', 'BLOCKED')
    $profiles = @('synthetic', 'desktop-local', 'real-readonly', 'real-game', 'ai-live', 'release')
    Test-Condition ($ledger.schema_version -eq 1) 'Unsupported ledger schema_version'
    Test-Condition ($tasks.Count -gt 0) 'No tasks'
    foreach ($task in $tasks) {
        Test-Condition ($task.id -cmatch '^PS-\d{2}-\d{2}$') "Invalid task ID: $($task.id)"
        Test-Condition (-not $taskMap.ContainsKey($task.id)) "Duplicate task: $($task.id)"
        $taskMap[$task.id] = $task
        Test-Condition ($task.status -cin $allowedStatus) "Invalid status: $($task.id)"
        Test-Condition ($task.verification -cin $allowedVerification) "Invalid verification: $($task.id)"
        Test-Condition (@($task.estimate_hours).Count -eq 2 -and $task.estimate_hours[0] -gt 0 -and $task.estimate_hours[1] -ge $task.estimate_hours[0]) "Invalid estimate: $($task.id)"
        Test-Condition (@($task.requirements).Count -gt 0) "No requirements: $($task.id)"
        Test-Condition (@($task.cases).Count -gt 0) "No cases: $($task.id)"
        Test-Condition (@($task.verification_profiles).Count -gt 0) "No verification profiles: $($task.id)"
        foreach ($profile in $task.verification_profiles) {
            Test-Condition ($profile -cin $profiles) "Unknown profile $profile in $($task.id)"
        }
        Test-Condition ($task.card -ceq "tasks/$($task.id).md") "Unexpected task-card path: $($task.id)"
        $card = Read-Doc $task.card
        Test-Condition ($card.StartsWith("# $($task.id) $($task.title)")) "Task title mismatch: $($task.id)"
        foreach ($heading in @('定位与前置条件','目标与实际消费者','必读配套文档','文件范围','实现步骤','验收标准','验证内容与命令','资源与排除范围','证据与交接')) {
            Test-Condition ($card.Contains("## $heading")) "Missing card section '$heading': $($task.id)"
        }
        foreach ($ref in @($task.requirements) + @($task.depends_on) + @($task.cases)) {
            Test-Condition ($card.Contains($ref)) "Card omits $ref : $($task.id)"
        }
        foreach ($profile in $task.verification_profiles) {
            Test-Condition ($card.Contains($profile)) "Card omits profile $profile : $($task.id)"
        }
        if ($task.status -eq 'VERIFIED') {
            Test-Condition ($task.verification -eq 'PASS') "VERIFIED without PASS: $($task.id)"
            Test-Condition (@($task.evidence).Count -gt 0) "VERIFIED without evidence: $($task.id)"
        }
        if ($task.status -eq 'BLOCKED') {
            Test-Condition (-not [string]::IsNullOrWhiteSpace($task.blocked_reason)) "BLOCKED without reason: $($task.id)"
        }
        foreach ($evidence in $task.evidence) {
            Test-Condition (Test-Path -LiteralPath (Join-Path $resolvedDocs $evidence) -PathType Leaf) "Missing task evidence: $($task.id): $evidence"
        }
    }
    $position = 0
    foreach ($id in $ledger.execution_order) {
        Test-Condition ($taskMap.ContainsKey($id)) "Unknown task in execution_order: $id"
        Test-Condition (-not $orderMap.ContainsKey($id)) "Duplicate execution_order ID: $id"
        $orderMap[$id] = $position++
    }
    Test-Condition ($orderMap.Count -eq $tasks.Count) 'Execution order does not cover all tasks'
    foreach ($task in $tasks) {
        foreach ($dep in $task.depends_on) {
            Test-Condition ($taskMap.ContainsKey($dep)) "Unknown dependency $dep in $($task.id)"
            if ($orderMap.ContainsKey($dep) -and $orderMap.ContainsKey($task.id)) {
                Test-Condition ($orderMap[$dep] -lt $orderMap[$task.id]) "Cycle or non-topological order: $dep -> $($task.id)"
            }
        }
    }

    $gateMap = @{}
    foreach ($gate in $ledger.gates) {
        Test-Condition (-not $gateMap.ContainsKey($gate.id)) "Duplicate gate: $($gate.id)"
        $gateMap[$gate.id] = $gate
        Test-Condition ($gate.status -cin $allowedVerification) "Invalid gate status: $($gate.id)"
        Test-Condition ($taskMap.ContainsKey($gate.after_task)) "Unknown gate exit task: $($gate.id)"
        foreach ($id in $gate.required_tasks) {
            Test-Condition ($taskMap.ContainsKey($id)) "Unknown required task: $($gate.id): $id"
            if ($taskMap.ContainsKey($id)) {
                Test-Condition ($taskMap[$id].milestone -eq $gate.milestone) "Gate task belongs to other milestone: $id"
                if ($gate.status -eq 'PASS') {
                    Test-Condition ($taskMap[$id].status -eq 'VERIFIED' -and $taskMap[$id].verification -eq 'PASS') "PASS gate with unverified task: $($gate.id): $id"
                }
            }
        }
        if ($gate.status -eq 'PASS') { Test-Condition (@($gate.evidence).Count -gt 0) "PASS gate without evidence: $($gate.id)" }
        foreach ($evidence in $gate.evidence) {
            Test-Condition (Test-Path -LiteralPath (Join-Path $resolvedDocs $evidence) -PathType Leaf) "Missing gate evidence: $($gate.id): $evidence"
        }
    }
    foreach ($gate in $ledger.gates) {
        foreach ($dep in $gate.requires_gates) {
            Test-Condition ($gateMap.ContainsKey($dep)) "Unknown predecessor gate: $dep"
            if ($gateMap.ContainsKey($dep)) {
                Test-Condition ([int]$dep.Substring(1) -lt [int]$gate.id.Substring(1)) "Gate cycle: $dep -> $($gate.id)"
                if ($gate.status -eq 'PASS') { Test-Condition ($gateMap[$dep].status -eq 'PASS') "PASS gate with incomplete predecessor: $($gate.id)" }
            }
        }
    }
    foreach ($task in $tasks) {
        if ($null -ne $task.required_gate) {
            Test-Condition ($gateMap.ContainsKey($task.required_gate)) "Unknown required gate: $($task.id)"
            if ($gateMap.ContainsKey($task.required_gate)) {
                $gate = $gateMap[$task.required_gate]
                Test-Condition ($orderMap[$gate.after_task] -lt $orderMap[$task.id]) "Implicit gate dependency cycle: $($task.id)"
            }
        }
    }
    $milestoneTasks = @()
    foreach ($milestone in $ledger.milestones) {
        foreach ($id in $milestone.tasks) {
            $milestoneTasks += $id
            Test-Condition ($taskMap.ContainsKey($id)) "Unknown milestone task: $id"
            if ($taskMap.ContainsKey($id)) { Test-Condition ($taskMap[$id].milestone -eq $milestone.id) "Milestone mismatch: $id" }
        }
        if ($null -ne $milestone.gate) {
            Test-Condition ($gateMap.ContainsKey($milestone.gate)) "Unknown milestone gate: $($milestone.id)"
            Test-Condition ($milestone.exit_task -eq @($milestone.tasks)[-1]) "Exit task is not milestone end: $($milestone.id)"
            if ($gateMap.ContainsKey($milestone.gate)) {
                $gate = $gateMap[$milestone.gate]
                Test-Condition ($gate.after_task -eq $milestone.exit_task) "Gate/milestone exit mismatch: $($milestone.id)"
                Test-Condition (@(Compare-Object @($milestone.tasks) @($gate.required_tasks)).Count -eq 0) "Gate omits milestone tasks: $($milestone.id)"
            }
        }
    }
    Test-Condition ($milestoneTasks.Count -eq $tasks.Count -and @($milestoneTasks | Sort-Object -Unique).Count -eq $tasks.Count) 'Milestone coverage incomplete or duplicated'

    $scope = Read-Doc '01-product-scope.md'
    $reqIds = @([regex]::Matches($scope, '(?m)^\| ((?:N?FR)-\d{3}) \|') | ForEach-Object { $_.Groups[1].Value })
    $reqMap = @{}
    foreach ($req in $ledger.requirements) {
        Test-Condition (-not $reqMap.ContainsKey($req.id)) "Duplicate requirement: $($req.id)"
        $reqMap[$req.id] = $req
        Test-Condition ($req.id -cin $reqIds) "Undocumented requirement: $($req.id)"
        Test-Condition (@($req.tasks).Count -gt 0) "Requirement without owner: $($req.id)"
        foreach ($id in $req.tasks) {
            Test-Condition ($taskMap.ContainsKey($id)) "Unknown requirement owner: $id"
            if ($taskMap.ContainsKey($id)) { Test-Condition ($req.id -cin $taskMap[$id].requirements) "Requirement mapping not reciprocal: $($req.id): $id" }
        }
    }
    foreach ($id in $reqIds) { Test-Condition ($reqMap.ContainsKey($id)) "Requirement missing from ledger: $id" }
    foreach ($task in $tasks) {
        foreach ($id in $task.requirements) {
            Test-Condition ($reqMap.ContainsKey($id)) "Unknown requirement: $($task.id): $id"
            if ($reqMap.ContainsKey($id)) { Test-Condition ($task.id -cin $reqMap[$id].tasks) "Task requirement not reciprocal: $($task.id): $id" }
        }
    }

    $matrix = Read-Doc '07-validation-matrix.md'
    $caseMap = @{}
    foreach ($match in [regex]::Matches($matrix, '(?m)^\| (CASE-\d{3}) \| ([^|]+)\|')) {
        $id = $match.Groups[1].Value
        Test-Condition (-not $caseMap.ContainsKey($id)) "Duplicate case: $id"
        $owners = @([regex]::Matches($match.Groups[2].Value, 'PS-\d{2}-\d{2}') | ForEach-Object Value)
        $caseMap[$id] = $owners
        foreach ($owner in $owners) {
            Test-Condition ($taskMap.ContainsKey($owner)) "Unknown case owner: $id : $owner"
            if ($taskMap.ContainsKey($owner)) { Test-Condition ($id -cin $taskMap[$owner].cases) "Matrix case not mapped in ledger: $id : $owner" }
        }
    }
    foreach ($task in $tasks) {
        foreach ($id in $task.cases) {
            Test-Condition ($caseMap.ContainsKey($id)) "Missing case: $($task.id): $id"
            if ($caseMap.ContainsKey($id)) { Test-Condition ($task.id -cin $caseMap[$id]) "Case owner mismatch: $($task.id): $id" }
        }
    }

    $prompts = Read-Doc '11-copy-paste-prompts.md'
    $roadmap = Read-Doc '09-roadmap.md'
    $promptIds = @([regex]::Matches($prompts, '(?m)^### (PS-\d{2}-\d{2}) ') | ForEach-Object { $_.Groups[1].Value })
    Test-Condition (($promptIds -join ',') -ceq (@($ledger.execution_order) -join ',')) 'Prompt order/coverage differs from execution_order'
    foreach ($task in $tasks) { Test-Condition ($roadmap.Contains($task.card)) "Roadmap missing card: $($task.id)" }
    foreach ($gate in $ledger.gates) { Test-Condition ($prompts.Contains("### $($gate.id) 阶段检查")) "Missing gate prompt: $($gate.id)" }

    # Check maintained documents only. Historical evidence and before/ snapshots
    # may contain intentionally preserved old links or permission-denied fixtures.
    $markdownFiles = @(
        Get-ChildItem -LiteralPath $resolvedDocs -Filter '*.md' -File
        Get-ChildItem -LiteralPath (Join-Path $resolvedDocs 'tasks') -Filter '*.md' -File
        $evidenceGuide = Join-Path $resolvedDocs 'evidence/README.md'
        if (Test-Path -LiteralPath $evidenceGuide -PathType Leaf) { Get-Item -LiteralPath $evidenceGuide }
        $changesRoot = Join-Path $resolvedDocs 'changes'
        if (Test-Path -LiteralPath $changesRoot -PathType Container) {
            foreach ($change in Get-ChildItem -LiteralPath $changesRoot -Directory) {
                Get-ChildItem -LiteralPath $change.FullName -Filter '*.md' -File
            }
        }
    )
    $linkCount = 0
    foreach ($file in $markdownFiles) {
        $content = [IO.File]::ReadAllText($file.FullName)
        if ($Probe -eq 'MissingLocalLink' -and $file.FullName -eq (Join-Path $resolvedDocs 'README.md')) {
            $content += "`n[Negative probe](missing-probe-file.md)`n"
        }
        Test-Condition (([regex]::Matches($content, '(?m)^```').Count % 2) -eq 0) "Unbalanced code fence: $($file.FullName)"
        foreach ($match in [regex]::Matches($content, '\[[^\]\r\n]+\]\(([^)\r\n]+)\)')) {
            $target = $match.Groups[1].Value.Trim().Trim('<', '>')
            if ($target -match '^(https?://|mailto:|#|app://|codex://)') { continue }
            $target = ($target -split '#', 2)[0]
            if ([string]::IsNullOrWhiteSpace($target)) { continue }
            $linkCount++
            $targetPath = [IO.Path]::GetFullPath((Join-Path $file.DirectoryName ([Uri]::UnescapeDataString($target))))
            Test-Condition (Test-Path -LiteralPath $targetPath) "Broken local link in $($file.Name): $target"
        }
    }
    $cardFiles = @(Get-ChildItem -LiteralPath (Join-Path $resolvedDocs 'tasks') -Filter '*.md' -File)
    Test-Condition ($cardFiles.Count -eq $tasks.Count) 'Task-card count differs from ledger'
    foreach ($file in $cardFiles) { Test-Condition ($taskMap.ContainsKey($file.BaseName)) "Orphan card: $($file.Name)" }

    $result = [ordered]@{
        verification_kind = 'documentation_structure'
        validation_probe = $Probe
        checked_at = [DateTime]::UtcNow.ToString('o')
        status = $(if ($issues.Count -eq 0) { 'PASS' } else { 'FAIL' })
        checks = $checks
        tasks = $tasks.Count
        legacy_m0_to_m5_tasks = @($tasks | Where-Object milestone -ne 'M6').Count
        deferred_tasks = @($tasks | Where-Object status -eq 'DEFERRED').Count
        requirements = $reqIds.Count
        cases = $caseMap.Count
        gates = @($ledger.gates).Count
        enforced_gates = @($ledger.gates | Where-Object { -not $_.PSObject.Properties['enforced'] -or $_.enforced }).Count
        markdown_files = $markdownFiles.Count
        markdown_scope = 'Current documents, task cards, evidence guide and change notes; historical evidence and before/ snapshots excluded'
        local_links = $linkCount
        issues = @($issues.ToArray())
        product_verification = 'NOT_EVALUATED'
        limitations = @('No product tests or game execution', 'Historical evidence references are checked for file existence only; their results are not revalidated', 'External URLs and link fragments are not fetched or validated')
    }
    $json = $result | ConvertTo-Json -Depth 8
    if ($ReportPath) {
        $reportAbsolute = [IO.Path]::GetFullPath($ReportPath)
        $approvedPrefix = $resolvedDocs.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
        if (-not $reportAbsolute.StartsWith($approvedPrefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'ReportPath must stay within DocsRoot' }
        $reportParent = Split-Path -Parent $reportAbsolute
        if (-not (Test-Path -LiteralPath $reportParent -PathType Container)) { throw 'Report parent must already exist' }
        [IO.File]::WriteAllText($reportAbsolute, $json + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
    }
    Write-Output $json
    if ($issues.Count -gt 0) { exit 1 }
    exit 0
}
catch {
    Write-Error ("Documentation validation failed: " + $_.Exception.Message) -ErrorAction Continue
    exit 2
}
