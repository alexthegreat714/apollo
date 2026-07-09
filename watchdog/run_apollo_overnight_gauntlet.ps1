param(
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..\..")
$apolloRoot = Join-Path $repoRoot "Apollo"
$runStamp = Get-Date -Format "yyyyMMdd_HHmmss"
$runDate = Get-Date -Format "yyyy-MM-dd"
$runRoot = Join-Path $apolloRoot "logs\overnight_gauntlet\$runDate\gauntlet_$runStamp"
$latestDir = Join-Path $apolloRoot "logs\overnight_gauntlet"
$latestStatusPath = Join-Path $latestDir "latest_status.json"
$latestReportPath = Join-Path $latestDir "latest_report.md"
$lockPath = Join-Path $latestDir "apollo_overnight_gauntlet.lock"
$phaseIndex = 0
$phaseResults = New-Object System.Collections.Generic.List[object]

New-Item -ItemType Directory -Force -Path $runRoot | Out-Null
New-Item -ItemType Directory -Force -Path $latestDir | Out-Null

function ConvertTo-SafeSlug {
  param([string]$Value)
  return (($Value.ToLowerInvariant() -replace "[^a-z0-9]+", "_").Trim("_"))
}

function Resolve-Python {
  $candidates = @(
    "C:\Users\blyth\AppData\Local\Programs\Python\Python312\python.exe",
    "C:\Users\blyth\AppData\Local\Programs\Python\Python311\python.exe"
  )
  foreach ($candidate in $candidates) {
    if (Test-Path -LiteralPath $candidate) { return $candidate }
  }
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if ($cmd -and $cmd.Path) { return $cmd.Path }
  throw "python_not_found"
}

function Read-JsonObject {
  param([string]$Path)
  if (-not (Test-Path -LiteralPath $Path)) { return $null }
  try {
    return Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
  } catch {
    return $null
  }
}

function Get-TaskInfoObject {
  param([string]$TaskName)
  try {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    $info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction Stop
    return [ordered]@{
      task_name = $TaskName
      state = [string]$task.State
      last_run_time = if ($info.LastRunTime) { $info.LastRunTime.ToString("o") } else { "" }
      next_run_time = if ($info.NextRunTime) { $info.NextRunTime.ToString("o") } else { "" }
      last_task_result = [int]$info.LastTaskResult
    }
  } catch {
    return [ordered]@{
      task_name = $TaskName
      state = "missing"
      error = [string]$_
    }
  }
}

function Write-GauntletStatus {
  param([string]$State)
  $payload = [ordered]@{
    ok = ($State -eq "complete")
    state = $State
    run_date = $runDate
    run_stamp = $runStamp
    started_at = $script:startedAt
    updated_at = (Get-Date).ToString("o")
    run_root = $runRoot
    latest_report = $latestReportPath
    dry_run = $DryRun.IsPresent
    phases = @($phaseResults.ToArray())
  }
  $payload | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $latestStatusPath -Encoding UTF8
}

function Write-PhaseReport {
  param(
    [string]$Name,
    [string]$Title,
    [hashtable]$Details
  )
  $script:phaseIndex += 1
  $slug = ConvertTo-SafeSlug $Name
  $prefix = "{0:D2}_{1}" -f $script:phaseIndex, $slug
  $jsonPath = Join-Path $runRoot "$prefix.json"
  $mdPath = Join-Path $runRoot "$prefix.md"
  $ok = if ($Details.ContainsKey("ok")) { [bool]$Details["ok"] } else { $true }

  $payload = [ordered]@{
    phase = $script:phaseIndex
    name = $Name
    title = $Title
    ok = $ok
    started_at = if ($Details.ContainsKey("started_at")) { $Details.started_at } else { "" }
    finished_at = if ($Details.ContainsKey("finished_at")) { $Details.finished_at } else { (Get-Date).ToString("o") }
    report_path = $mdPath
    details = $Details
  }
  $payload | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $jsonPath -Encoding UTF8

  $lines = New-Object System.Collections.Generic.List[string]
  $lines.Add("# Apollo Overnight Phase $($script:phaseIndex): $Title")
  $lines.Add("")
  $lines.Add("- status: $(if ($ok) { 'ok' } else { 'failed_or_warn' })")
  $lines.Add("- generated_at: $((Get-Date).ToString("o"))")
  foreach ($key in ($Details.Keys | Sort-Object)) {
    $value = $Details[$key]
    if ($key -eq "markdown" -or $key -eq "raw") { continue }
    if ($value -is [string] -or $value -is [int] -or $value -is [double] -or $value -is [bool]) {
      $lines.Add("- $($key): $value")
    }
  }
  if ($Details.ContainsKey("markdown") -and -not [string]::IsNullOrWhiteSpace([string]$Details.markdown)) {
    $lines.Add("")
    $lines.Add([string]$Details.markdown)
  }
  $lines -join [Environment]::NewLine | Set-Content -LiteralPath $mdPath -Encoding UTF8

  $phaseResults.Add([ordered]@{
    phase = $script:phaseIndex
    name = $Name
    title = $Title
    ok = $ok
    json = $jsonPath
    report = $mdPath
    finished_at = $payload.finished_at
  }) | Out-Null
  Write-GauntletStatus -State "running"
}

function Invoke-TrackedProcess {
  param(
    [string]$Name,
    [string]$Executable,
    [string[]]$Arguments,
    [int]$TimeoutMinutes = 60
  )
  $slug = ConvertTo-SafeSlug $Name
  $stdout = Join-Path $runRoot "$slug.stdout.log"
  $stderr = Join-Path $runRoot "$slug.stderr.log"
  $started = Get-Date
  if ($DryRun) {
    return @{
      ok = $true
      dry_run = $true
      executable = $Executable
      arguments = ($Arguments -join " ")
      stdout = $stdout
      stderr = $stderr
      started_at = $started.ToString("o")
      finished_at = (Get-Date).ToString("o")
      exit_code = 0
    }
  }
  $proc = Start-Process `
    -FilePath $Executable `
    -ArgumentList $Arguments `
    -WindowStyle Hidden `
    -PassThru `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr
  $completed = $proc.WaitForExit([Math]::Max(1, $TimeoutMinutes) * 60 * 1000)
  if (-not $completed) {
    try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch {}
    return @{
      ok = $false
      error = "timeout"
      timeout_minutes = $TimeoutMinutes
      executable = $Executable
      arguments = ($Arguments -join " ")
      stdout = $stdout
      stderr = $stderr
      started_at = $started.ToString("o")
      finished_at = (Get-Date).ToString("o")
      exit_code = 124
    }
  }
  try { $proc.Refresh() } catch {}
  $exitCode = if ($proc.ExitCode -ne $null) { [int]$proc.ExitCode } else { 1 }
  return @{
    ok = ($exitCode -eq 0)
    executable = $Executable
    arguments = ($Arguments -join " ")
    stdout = $stdout
    stderr = $stderr
    started_at = $started.ToString("o")
    finished_at = (Get-Date).ToString("o")
    exit_code = $exitCode
  }
}

function Wait-ForScheduledTaskOrFallback {
  param(
    [string]$TaskName,
    [datetime]$TargetTime,
    [string]$FallbackScript,
    [int]$GraceMinutes = 20,
    [int]$FallbackTimeoutMinutes = 90
  )
  $started = Get-Date
  $psExe = "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe"
  $fallbackRan = $false
  $fallback = $null
  $notes = New-Object System.Collections.Generic.List[string]

  if ($DryRun) {
    return @{
      ok = $true
      dry_run = $true
      task_name = $TaskName
      target_time = $TargetTime.ToString("o")
      fallback_script = $FallbackScript
      started_at = $started.ToString("o")
      finished_at = (Get-Date).ToString("o")
      markdown = "Dry run only. Would wait for task `$TaskName` and run fallback `$FallbackScript` if needed."
    }
  }

  while ((Get-Date) -lt $TargetTime) {
    Start-Sleep -Seconds ([Math]::Min(60, [Math]::Max(5, [int](($TargetTime - (Get-Date)).TotalSeconds))))
  }

  $deadline = $TargetTime.AddMinutes($GraceMinutes)
  do {
    $snapshot = Get-TaskInfoObject -TaskName $TaskName
    if ($snapshot.state -eq "Running") {
      $notes.Add("Task $TaskName is running at $((Get-Date).ToString('o')); waiting for completion.") | Out-Null
      Start-Sleep -Seconds 60
      continue
    }
    $lastRun = $null
    try { $lastRun = [datetime]$snapshot.last_run_time } catch {}
    if ($lastRun -and $lastRun -ge $TargetTime.AddMinutes(-5)) {
      if ([int]$snapshot.last_task_result -ne 0 -and (Test-Path -LiteralPath $FallbackScript)) {
        $fallbackRan = $true
        $fallback = Invoke-TrackedProcess -Name "retry_$TaskName" -Executable $psExe -Arguments @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $FallbackScript) -TimeoutMinutes $FallbackTimeoutMinutes
        return @{
          ok = [bool]$fallback["ok"]
          task_name = $TaskName
          target_time = $TargetTime.ToString("o")
          observed_task = $snapshot
          scheduled_result = [int]$snapshot.last_task_result
          fallback_ran = $true
          fallback_reason = "scheduled_task_nonzero"
          fallback = $fallback
          started_at = $started.ToString("o")
          finished_at = (Get-Date).ToString("o")
          markdown = "Observed scheduled task $TaskName run at $($snapshot.last_run_time) with nonzero result $($snapshot.last_task_result), so the gauntlet ran fallback script $FallbackScript once."
        }
      }
      return @{
        ok = ([int]$snapshot.last_task_result -eq 0)
        task_name = $TaskName
        target_time = $TargetTime.ToString("o")
        observed_task = $snapshot
        fallback_ran = $false
        started_at = $started.ToString("o")
        finished_at = (Get-Date).ToString("o")
        markdown = "Observed scheduled task $TaskName run at $($snapshot.last_run_time) with result $($snapshot.last_task_result)."
      }
    }
    if ((Get-Date) -lt $deadline) {
      Start-Sleep -Seconds 60
    }
  } while ((Get-Date) -lt $deadline)

  if (Test-Path -LiteralPath $FallbackScript) {
    $fallbackRan = $true
    $fallback = Invoke-TrackedProcess -Name "fallback_$TaskName" -Executable $psExe -Arguments @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $FallbackScript) -TimeoutMinutes $FallbackTimeoutMinutes
  }

  return @{
    ok = if ($fallbackRan) { [bool]$fallback["ok"] } else { $false }
    task_name = $TaskName
    target_time = $TargetTime.ToString("o")
    observed_task = Get-TaskInfoObject -TaskName $TaskName
    fallback_ran = $fallbackRan
    fallback = $fallback
    started_at = $started.ToString("o")
    finished_at = (Get-Date).ToString("o")
    markdown = if ($fallbackRan) { "Scheduled task $TaskName did not produce a fresh run by the grace window, so fallback script ran: $FallbackScript." } else { "Scheduled task $TaskName did not produce a fresh run and fallback script was missing: $FallbackScript." }
  }
}

function Get-NightlyQualityMarkdown {
  $statusPath = Join-Path $apolloRoot "logs\nightly\latest_status.json"
  $nightly = Read-JsonObject $statusPath
  if (-not $nightly) {
    return @{
      ok = $false
      status_path = $statusPath
      markdown = "No nightly status file was readable."
    }
  }
  $gather = $null
  if ($nightly.stage_details -and $nightly.stage_details.gather) { $gather = $nightly.stage_details.gather }
  $quality = $null
  if ($gather -and $gather.quality) { $quality = $gather.quality }
  $lines = New-Object System.Collections.Generic.List[string]
  $lines.Add("## Nightly Source Quality")
  $lines.Add("")
  $lines.Add("- run_id: $($nightly.run_id)")
  $lines.Add("- nightly_ok: $($nightly.ok)")
  $lines.Add("- gathered_sources: $($nightly.gathered_sources)")
  $lines.Add("- new_triples: $($nightly.new_triples)")
  if ($quality) {
    $lines.Add("- gather_score: $($quality.score)")
    $lines.Add("- source_confidence: $($quality.source_confidence)")
    $lines.Add("- c_only_run: $($quality.c_only_run)")
    $lines.Add("- standard_trade_ready_allowed: $($quality.standard_trade_ready_allowed)")
    $lines.Add("- trade_confidence_cap: $($quality.trade_confidence_cap)")
  }
  return @{
    ok = [bool]$nightly.ok
    status_path = $statusPath
    run_id = [string]$nightly.run_id
    gathered_sources = if ($nightly.gathered_sources -ne $null) { [int]$nightly.gathered_sources } else { 0 }
    new_triples = if ($nightly.new_triples -ne $null) { [int]$nightly.new_triples } else { 0 }
    markdown = ($lines -join [Environment]::NewLine)
  }
}

function Write-FinalReport {
  $lines = New-Object System.Collections.Generic.List[string]
  $lines.Add("# Apollo Overnight Gauntlet Report")
  $lines.Add("")
  $lines.Add("- run_date: $runDate")
  $lines.Add("- started_at: $script:startedAt")
  $lines.Add("- finished_at: $((Get-Date).ToString("o"))")
  $lines.Add("- run_root: $runRoot")
  $lines.Add("- dry_run: $($DryRun.IsPresent)")
  $lines.Add("")
  $lines.Add("## Phase Results")
  foreach ($phase in $phaseResults) {
    $status = if ($phase.ok) { "ok" } else { "failed_or_warn" }
    $lines.Add("- $($phase.phase). $($phase.title): $status")
    $lines.Add("  - report: $($phase.report)")
  }
  $lines.Add("")
  $lines.Add("## Morning Review")
  $lines.Add("- Latest gauntlet status: $latestStatusPath")
  $lines.Add("- Latest gauntlet report: $latestReportPath")
  $lines.Add("- Per-phase reports are in: $runRoot")
  $reportPath = Join-Path $runRoot "overnight_gauntlet_report.md"
  $lines -join [Environment]::NewLine | Set-Content -LiteralPath $reportPath -Encoding UTF8
  Copy-Item -LiteralPath $reportPath -Destination $latestReportPath -Force
}

$script:startedAt = (Get-Date).ToString("o")

if (Test-Path -LiteralPath $lockPath) {
  $lockAge = (Get-Date) - (Get-Item -LiteralPath $lockPath).LastWriteTime
  if ($lockAge.TotalHours -lt 10 -and -not $DryRun) {
    Write-PhaseReport -Name "lock_check" -Title "Existing Gauntlet Lock" -Details @{
      ok = $false
      lock_path = $lockPath
      lock_age_minutes = [math]::Round($lockAge.TotalMinutes, 1)
      started_at = $script:startedAt
      finished_at = (Get-Date).ToString("o")
      markdown = "An existing gauntlet lock is still fresh. This run will not start another heavy overnight sequence."
    }
    Write-FinalReport
    Write-GauntletStatus -State "blocked"
    exit 2
  }
  Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
}

"pid=$PID started=$script:startedAt run_root=$runRoot dry_run=$DryRun" | Set-Content -LiteralPath $lockPath -Encoding UTF8

try {
  $env:PYTHONPATH = [string]$repoRoot
  $env:APOLLO_EVENT_IMPACT_ENABLED = "1"
  $env:APOLLO_EVENT_ALLOW_PAPER_CANDIDATES = "1"
  $env:APOLLO_FOCUS_UNIVERSE_GB10_ONLY = "1"
  $env:APOLLO_GB10_OCR_ENABLED = "1"
  $env:APOLLO_FAST_LLM_BASE_URL = if ($env:APOLLO_FAST_LLM_BASE_URL) { $env:APOLLO_FAST_LLM_BASE_URL } else { "http://127.0.0.1:11434" }
  $env:APOLLO_FAST_LLM_MODEL = if ($env:APOLLO_FAST_LLM_MODEL) { $env:APOLLO_FAST_LLM_MODEL } else { "gemma3:12b" }
  $env:APOLLO_TRADE_REASON_LLM_BASE_URL = if ($env:APOLLO_TRADE_REASON_LLM_BASE_URL) { $env:APOLLO_TRADE_REASON_LLM_BASE_URL } else { "http://127.0.0.1:11434" }
  $env:APOLLO_TRADE_REASON_LLM_MODEL = if ($env:APOLLO_TRADE_REASON_LLM_MODEL) { $env:APOLLO_TRADE_REASON_LLM_MODEL } else { "qwen2.5:72b" }
  $env:APOLLO_TRADE_REASON_LLM_FALLBACKS = if ($env:APOLLO_TRADE_REASON_LLM_FALLBACKS) { $env:APOLLO_TRADE_REASON_LLM_FALLBACKS } else { "gemma-3-27b-it-Q4_K_M:latest,gemma-4-26b-a4b-q4km-ctx8:latest,gemma3:12b" }
  $env:APOLLO_MODEL_STORAGE_ROOT = if ($env:APOLLO_MODEL_STORAGE_ROOT) { $env:APOLLO_MODEL_STORAGE_ROOT } else { "D:\ApolloModels" }
  $env:APOLLO_ALLOW_C_DRIVE_MODEL_DOWNLOADS = if ($env:APOLLO_ALLOW_C_DRIVE_MODEL_DOWNLOADS) { $env:APOLLO_ALLOW_C_DRIVE_MODEL_DOWNLOADS } else { "0" }
  $env:OLLAMA_MODELS = if ($env:OLLAMA_MODELS) { $env:OLLAMA_MODELS } else { Join-Path $env:APOLLO_MODEL_STORAGE_ROOT "ollama" }
  $env:HF_HOME = if ($env:HF_HOME) { $env:HF_HOME } else { Join-Path $env:APOLLO_MODEL_STORAGE_ROOT "huggingface" }
  $env:TRANSFORMERS_CACHE = if ($env:TRANSFORMERS_CACHE) { $env:TRANSFORMERS_CACHE } else { Join-Path $env:HF_HOME "transformers" }
  $env:TORCH_HOME = if ($env:TORCH_HOME) { $env:TORCH_HOME } else { Join-Path $env:APOLLO_MODEL_STORAGE_ROOT "torch" }
  $env:OLLAMA_URL = if ($env:OLLAMA_URL) { $env:OLLAMA_URL } else { "http://127.0.0.1:11434" }
  $env:OLLAMA_URL_DEEP = if ($env:OLLAMA_URL_DEEP) { $env:OLLAMA_URL_DEEP } else { "http://127.0.0.1:11434" }
  $env:OLLAMA_MODEL_CHAT = if ($env:OLLAMA_MODEL_CHAT) { $env:OLLAMA_MODEL_CHAT } else { "gemma3:12b" }
  $env:OLLAMA_MODEL_DEEP = if ($env:OLLAMA_MODEL_DEEP) { $env:OLLAMA_MODEL_DEEP } else { "gemma4:31b" }

  $today = (Get-Date).Date
  $psExe = "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe"
  $pythonExe = Resolve-Python

  Write-GauntletStatus -State "running"
  Write-PhaseReport -Name "preflight" -Title "Preflight And Schedule Snapshot" -Details @{
    ok = $true
    started_at = $script:startedAt
    finished_at = (Get-Date).ToString("o")
    run_root = $runRoot
    python = $pythonExe
    event_impact_enabled = $env:APOLLO_EVENT_IMPACT_ENABLED
    paper_event_candidates = $env:APOLLO_EVENT_ALLOW_PAPER_CANDIDATES
    gb10_only = $env:APOLLO_FOCUS_UNIVERSE_GB10_ONLY
    markdown = "The gauntlet is sequenced around the existing Apollo scheduled tasks and will run wrapper fallbacks only when a fresh scheduled run is not observed."
  }

  $nightlyScript = Join-Path $scriptDir "run_apollo_nightly_daily.ps1"
  $nightlyTarget = $today.AddHours(1)
  Write-PhaseReport -Name "nightly_source_homework" -Title "1 AM Nightly Source Homework" -Details (Wait-ForScheduledTaskOrFallback -TaskName "ApolloNightlyPipeline0100" -TargetTime $nightlyTarget -FallbackScript $nightlyScript -GraceMinutes 20 -FallbackTimeoutMinutes 480)

  Write-PhaseReport -Name "source_quality_review" -Title "Nightly Gather Quality Review" -Details (Get-NightlyQualityMarkdown)

  $kgInfo = Get-TaskInfoObject -TaskName "ApolloHomeworkKGLLM0030"
  $nightlyQuality = Get-NightlyQualityMarkdown
  $shouldKgCatchup = (-not $DryRun) -and ([string]$kgInfo.last_task_result -ne "0") -and ((Get-Date) -lt $today.AddHours(3))
  if ($shouldKgCatchup) {
    $kgScript = Join-Path $scriptDir "run_apollo_kg_llm.ps1"
    Write-PhaseReport -Name "kg_llm_catchup" -Title "KG LLM Catch-Up Pass" -Details (Invoke-TrackedProcess -Name "kg_llm_catchup" -Executable $psExe -Arguments @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $kgScript) -TimeoutMinutes 75)
  } else {
    Write-PhaseReport -Name "kg_llm_catchup" -Title "KG LLM Catch-Up Pass" -Details @{
      ok = $true
      skipped = $true
      started_at = (Get-Date).ToString("o")
      finished_at = (Get-Date).ToString("o")
      kg_task_result = if ($kgInfo.Contains("last_task_result")) { [string]$kgInfo.last_task_result } else { "" }
      markdown = "Skipped manual KG LLM catch-up because the scheduled result was already clean, dry-run mode is active, or the 3:00 AM HippoRAG window was too close."
    }
  }

  $hippoScript = Join-Path $scriptDir "run_apollo_improved_hipporag_0315.ps1"
  Write-PhaseReport -Name "hipporag_0315" -Title "3:15 AM Improved HippoRAG" -Details (Wait-ForScheduledTaskOrFallback -TaskName "ApolloImprovedHippoRAG0315" -TargetTime $today.AddHours(3).AddMinutes(15) -FallbackScript $hippoScript -GraceMinutes 25 -FallbackTimeoutMinutes 180)

  $tradeScript = Join-Path $scriptDir "run_apollo_trade_cycle_0400.ps1"
  Write-PhaseReport -Name "trade_cycle_0400" -Title "4 AM Risk-Ramp Trade Cycle" -Details (Wait-ForScheduledTaskOrFallback -TaskName "ApolloTradeCycle" -TargetTime $today.AddHours(4) -FallbackScript $tradeScript -GraceMinutes 20 -FallbackTimeoutMinutes 120)

  $swingScript = Join-Path $scriptDir "run_apollo_swing_overnight.ps1"
  Write-PhaseReport -Name "swing_0530" -Title "5:30 AM Swing Study" -Details (Wait-ForScheduledTaskOrFallback -TaskName "ApolloHomeworkSwing0530" -TargetTime $today.AddHours(5).AddMinutes(30) -FallbackScript $swingScript -GraceMinutes 25 -FallbackTimeoutMinutes 120)

  $gateScript = Join-Path $scriptDir "check_overnight_gate.ps1"
  Write-PhaseReport -Name "overnight_gate_0615" -Title "6:15 AM Overnight Gate Check" -Details (Wait-ForScheduledTaskOrFallback -TaskName "ApolloOvernightGateCheck0615" -TargetTime $today.AddHours(6).AddMinutes(15) -FallbackScript $gateScript -GraceMinutes 20 -FallbackTimeoutMinutes 20)

  $marketScript = Join-Path $scriptDir "run_apollo_market_study_0730.ps1"
  Write-PhaseReport -Name "market_study_0730" -Title "7:30 AM Market Study" -Details (Wait-ForScheduledTaskOrFallback -TaskName "ApolloMarketStudy0730" -TargetTime $today.AddHours(7).AddMinutes(30) -FallbackScript $marketScript -GraceMinutes 20 -FallbackTimeoutMinutes 90)

  $dailyScript = Join-Path $scriptDir "run_apollo_daily_report.ps1"
  $dailyArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $dailyScript, "-Action", "open", "-Note", "Apollo overnight gauntlet completed. Review $latestReportPath and phase reports under $runRoot.")
  $dailyPhase = if ($DryRun) {
    Invoke-TrackedProcess -Name "daily_report_open" -Executable $psExe -Arguments $dailyArgs -TimeoutMinutes 20
  } else {
    while ((Get-Date) -lt $today.AddHours(7).AddMinutes(45)) { Start-Sleep -Seconds 60 }
    Invoke-TrackedProcess -Name "daily_report_open" -Executable $psExe -Arguments $dailyArgs -TimeoutMinutes 20
  }
  Write-PhaseReport -Name "daily_report_open" -Title "Daily Report Opened" -Details $dailyPhase

  Write-FinalReport
  Write-GauntletStatus -State "complete"
  exit 0
} catch {
  Write-PhaseReport -Name "gauntlet_error" -Title "Gauntlet Error" -Details @{
    ok = $false
    started_at = $script:startedAt
    finished_at = (Get-Date).ToString("o")
    error = [string]$_
    markdown = "The overnight gauntlet stopped on an uncaught error. Earlier phase reports remain available in `$runRoot`."
  }
  Write-FinalReport
  Write-GauntletStatus -State "error"
  exit 1
} finally {
  Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
}
