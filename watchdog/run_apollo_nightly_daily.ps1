param()
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "scheduler_state.ps1")

$apolloRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$repoRoot = (Resolve-Path (Join-Path $apolloRoot "..")).Path
$pythonExe = "C:\Users\blyth\AppData\Local\Programs\Python\Python312\python.exe"
$schedulerRoot = Join-Path $apolloRoot "logs\nightly\scheduler_runs"
$manifestDir = Join-Path $schedulerRoot "manifests"
$summaryDir = Join-Path $schedulerRoot "summaries"
$latestManifestPath = Join-Path $schedulerRoot "latest_scheduler_run.json"
$nightlyLockPath = Join-Path $apolloRoot "logs\nightly\nightly.lock"
New-Item -ItemType Directory -Force -Path $schedulerRoot, $manifestDir, $summaryDir | Out-Null

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$runId = "daily_nightly_${stamp}"
$payloadPath = Join-Path $schedulerRoot "payload_${stamp}.json"
$stdout = Join-Path $schedulerRoot "nightly_${stamp}.stdout.log"
$stderr = Join-Path $schedulerRoot "nightly_${stamp}.stderr.log"
$manifestPath = Join-Path $manifestDir "nightly_${stamp}.json"
$summaryPath = Join-Path $summaryDir "nightly_${stamp}.json"
$script:manifest = $null

function Get-UtcNow {
    return (Get-Date).ToUniversalTime().ToString("o")
}

function Write-JsonAtomic {
    param([string]$Path, [object]$Payload)
    Write-ApolloJsonAtomic -Path $Path -Payload $Payload
}

function Save-Manifest {
    $script:manifest["updated_at"] = Get-UtcNow
    Write-JsonAtomic -Path $manifestPath -Payload $script:manifest
    Write-JsonAtomic -Path $latestManifestPath -Payload $script:manifest
}

function Get-ProcessRecord {
    param([int]$ProcessId)
    if ($ProcessId -le 0) { return $null }
    return Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
}

function Test-ProcessIdentity {
    param([object]$ProcessRecord, [string[]]$ExpectedTokens)
    if ($null -eq $ProcessRecord) { return $false }
    return Test-ApolloCommandIdentity -CommandLine ([string]$ProcessRecord.CommandLine) -ExpectedTokens $ExpectedTokens
}

function Get-NightlyLockState {
    if (-not (Test-Path $nightlyLockPath)) {
        return @{ present = $false; stale = $true; heartbeat_at = ""; pid = 0 }
    }
    try {
        $payload = Get-Content -LiteralPath $nightlyLockPath -Raw | ConvertFrom-Json
        $heartbeatRaw = [string]($payload.heartbeat_at)
        if ([string]::IsNullOrWhiteSpace($heartbeatRaw)) { $heartbeatRaw = [string]($payload.started_at) }
        $ageMinutes = if ($heartbeatRaw) { ((Get-Date).ToUniversalTime() - [DateTimeOffset]::Parse($heartbeatRaw).UtcDateTime).TotalMinutes } else { 999999 }
        return @{
            present = $true
            stale = ($ageMinutes -gt 15)
            heartbeat_at = $heartbeatRaw
            pid = [int]($payload.owner_pid)
        }
    } catch {
        return @{ present = $true; stale = $true; heartbeat_at = ""; pid = 0; error = $_.Exception.Message }
    }
}

function Set-PreviousManifestState {
    param([object]$Previous, [string]$State, [string]$Reason)
    $table = [ordered]@{}
    foreach ($property in $Previous.PSObject.Properties) { $table[$property.Name] = $property.Value }
    $table["state"] = $State
    $table["recovery_reason"] = $Reason
    $table["finished_at"] = Get-UtcNow
    $table["updated_at"] = Get-UtcNow
    $previousPath = [string]$Previous.manifest_path
    if ($previousPath -and (Test-Path (Split-Path -Parent $previousPath))) {
        Write-JsonAtomic -Path $previousPath -Payload $table
    }
    Write-JsonAtomic -Path $latestManifestPath -Payload $table
}

function Resolve-PreviousRun {
    if (-not (Test-Path $latestManifestPath)) { return }
    try {
        $previous = Get-Content -LiteralPath $latestManifestPath -Raw | ConvertFrom-Json
    } catch {
        return
    }
    if ([string]$previous.state -notin @("starting", "running")) { return }
    $heartbeatRaw = [string]$previous.heartbeat_at
    $heartbeatAge = if ($heartbeatRaw) { ((Get-Date).ToUniversalTime() - [DateTimeOffset]::Parse($heartbeatRaw).UtcDateTime).TotalMinutes } else { 999999 }
    $record = Get-ProcessRecord -ProcessId ([int]$previous.child_pid)
    $tokens = @($previous.expected_command_tokens | ForEach-Object { [string]$_ })
    $owned = Test-ProcessIdentity -ProcessRecord $record -ExpectedTokens $tokens
    $lockState = Get-NightlyLockState
    $decision = Get-ApolloSchedulerDecision `
        -ManifestState ([string]$previous.state) `
        -HeartbeatAgeMinutes $heartbeatAge `
        -LockStale ([bool]$lockState.stale) `
        -ProcessExists ($null -ne $record) `
        -ProcessIdentityMatches $owned

    if ($decision.classification -eq "abandoned") {
        Set-PreviousManifestState -Previous $previous -State "abandoned" -Reason "recorded_child_not_running"
        return
    }
    if ($decision.classification -eq "abandoned_identity_mismatch") {
        Set-PreviousManifestState -Previous $previous -State "abandoned_identity_mismatch" -Reason "pid_reused_or_command_changed_no_termination"
        return
    }
    if (-not $decision.may_terminate) {
        throw "previous_apollo_nightly_run_is_still_active:$($previous.run_id)"
    }

    & taskkill.exe /PID ([int]$previous.child_pid) /T /F | Out-Null
    Set-PreviousManifestState -Previous $previous -State "recovered_stale" -Reason "stale_manifest_and_lock_exact_process_tree_terminated"
}

function Remove-ExpiredSchedulerFiles {
    $now = Get-Date
    Get-ChildItem -LiteralPath $schedulerRoot -File -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt $now.AddDays(-30) -and $_.Name -ne "latest_scheduler_run.json" } |
        Remove-Item -Force
    Get-ChildItem -LiteralPath $manifestDir -File -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt $now.AddDays(-30) } |
        Remove-Item -Force
    Get-ChildItem -LiteralPath $summaryDir -File -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt $now.AddDays(-90) } |
        Remove-Item -Force
}

Resolve-PreviousRun
Remove-ExpiredSchedulerFiles

$env:OMP_NUM_THREADS = "4"
$env:OPENBLAS_NUM_THREADS = "4"
$env:MKL_NUM_THREADS = "4"
$env:NUMEXPR_NUM_THREADS = "4"
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
$env:APOLLO_SIM_MAIN_LANE = "aggressive_news_cycle"
$env:APOLLO_SIM_BROKER_PROVIDER = "paper_sim"
$env:APOLLO_SIM_EXECUTION_MODE = "paper"
$env:APOLLO_SIM_LIVE_ENABLED = "0"
$hipporagModel = if ($env:APOLLO_HIPPORAG_LLM_MODEL) { $env:APOLLO_HIPPORAG_LLM_MODEL } else { $env:APOLLO_FAST_LLM_MODEL }
$nightlyHipporagUseLlm = if ($env:APOLLO_NIGHTLY_HIPPORAG_USE_LLM) { @("1", "true", "yes", "on") -contains $env:APOLLO_NIGHTLY_HIPPORAG_USE_LLM.ToLowerInvariant() } else { $false }

$payload = [ordered]@{
    run_mode = "nightly"
    batch_id = $runId
    topic = "one discretionary stock trade decision per day with overnight holding allowed"
    max_articles = 6
    equity_news_tickers = @("NVDA","AMD","AVGO","TSM","MSFT","AMZN","GOOGL","META","JPM","GS","BAC","XOM","CVX","LLY","UNH","JNJ","CAT","HON")
    hipporag_use_llm = $nightlyHipporagUseLlm
    hipporag_llm_model = $hipporagModel
    allow_cpu_fallback = $false
    focus_universe_enabled = $false
    focus_universe_gb10_only = $true
    focus_universe_decision_min_volume = 60
    allow_inbox = $false
    gather_allow_seed_fallback = $true
    gather_web_rate_limit_retries = 1
    ocr_route_mode = "quality_first"
    memory_guard_enabled = $true
    memory_min_available_mb = 4096
    memory_max_used_percent = 92
    memory_max_vmmem_mb = 12288
    memory_max_ollama_runners = 4
    preflight_max_wait_sec = 1800
    overall_timeout_sec = 28800
}
Write-JsonAtomic -Path $payloadPath -Payload $payload

$script:manifest = [ordered]@{
    schema_version = 1
    task_name = "ApolloNightlyPipeline0100"
    run_id = $runId
    manifest_path = $manifestPath
    state = "starting"
    wrapper_pid = $PID
    child_pid = 0
    expected_command_tokens = @("-m Apollo.nightly_pipeline", (Split-Path -Leaf $payloadPath))
    started_at = Get-UtcNow
    heartbeat_at = Get-UtcNow
    finished_at = ""
    current_stage = "starting"
    pipeline_exit_code = $null
    supplemental = [ordered]@{}
    stdout = $stdout
    stderr = $stderr
    live_trade_execution = $false
}
Save-Manifest

$nightlyExitCode = 1
try {
    $arguments = @("-m", "Apollo.nightly_pipeline", "run", "--payload-file", $payloadPath)
    $process = Start-Process -FilePath $pythonExe -ArgumentList $arguments -WorkingDirectory $repoRoot -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    $script:manifest["child_pid"] = $process.Id
    $script:manifest["state"] = "running"
    $script:manifest["current_stage"] = "pipeline"
    Save-Manifest

    while (-not $process.WaitForExit(30000)) {
        $script:manifest["heartbeat_at"] = Get-UtcNow
        $latestPipelineStatus = Join-Path $apolloRoot "logs\nightly\latest_status.json"
        if (Test-Path $latestPipelineStatus) {
            try {
                $pipelineStatus = Get-Content -LiteralPath $latestPipelineStatus -Raw | ConvertFrom-Json
                $stage = [string]($pipelineStatus.current_stage)
                if ($stage) { $script:manifest["current_stage"] = $stage }
                $script:manifest["pipeline_run_id"] = [string]($pipelineStatus.run_id)
            } catch { }
        }
        Save-Manifest
    }
    $nightlyExitCode = [int]$process.ExitCode
    $script:manifest["pipeline_exit_code"] = $nightlyExitCode
    $script:manifest["heartbeat_at"] = Get-UtcNow
} catch {
    Add-Content -LiteralPath $stderr -Value ("nightly_wrapper_failed: " + $_.Exception.Message) -Encoding UTF8
    $script:manifest["error"] = $_.Exception.Message
    $nightlyExitCode = 1
}

$script:manifest["current_stage"] = "post_nightly_hipporag"
$hippoWrapper = Join-Path $apolloRoot "watchdog\run_apollo_improved_hipporag_0315.ps1"
if (Test-Path $hippoWrapper) {
    $hippoStdout = Join-Path $schedulerRoot "post_nightly_hipporag_${stamp}.stdout.log"
    $hippoStderr = Join-Path $schedulerRoot "post_nightly_hipporag_${stamp}.stderr.log"
    & "C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe" -WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File $hippoWrapper 1> $hippoStdout 2> $hippoStderr
    $hippoExitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 1 }
    $script:manifest["supplemental"]["hipporag_exit_code"] = $hippoExitCode
    if ($hippoExitCode -ne 0) {
        Add-Content -LiteralPath $stderr -Value "Post-nightly HippoRAG refresh exited with code $hippoExitCode. See $hippoStderr" -Encoding UTF8
    }
}

$script:manifest["current_stage"] = "calendar_enrichment_schedule"
try {
    $eventStart = (Get-Date).Date.AddHours(23)
    $eventEnd = $eventStart.AddHours(2)
    $eventDate = $eventStart.ToString("yyyy-MM-dd")
    $enrichmentScript = Join-Path $apolloRoot "watchdog\run_apollo_curated_enrichment.ps1"
    try {
        $events = (Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:5011/calendar/events" -TimeoutSec 8).events
    } catch {
        $events = @()
    }
    $alreadyExists = @($events | Where-Object {
        [string]$_.title -like "*Curated KG Enrichment*" -and [string]$_.start -like "*$eventDate*"
    }).Count -gt 0
    if (-not $alreadyExists) {
        $eventPayload = @{
            title = "Apollo Curated KG Enrichment (qwen3-vl:32b)"
            start = $eventStart.ToString("o")
            end = $eventEnd.ToString("o")
            status = "pending"
            source = "apollo"
            category = "apollo"
            trigger = "Scheduled curated HippoRAG enrichment for $eventDate. Runs $enrichmentScript"
            notes = "Curated KG enrichment: priority-scored chunks, qwen3-vl:32b, confidence>=7, deduplicated against existing edges."
            gb10_expected_percent = 85
        } | ConvertTo-Json -Depth 5
        Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:5011/calendar/events" -ContentType "application/json" -Body $eventPayload -TimeoutSec 10 | Out-Null
        $script:manifest["supplemental"]["calendar_status"] = "posted"
    } else {
        $script:manifest["supplemental"]["calendar_status"] = "already_present"
    }
} catch {
    $script:manifest["supplemental"]["calendar_status"] = "unavailable"
    $script:manifest["supplemental"]["calendar_error"] = $_.Exception.Message
}

$script:manifest["state"] = if ($nightlyExitCode -eq 0) { "succeeded" } else { "failed" }
$script:manifest["current_stage"] = "done"
$script:manifest["finished_at"] = Get-UtcNow
$script:manifest["heartbeat_at"] = Get-UtcNow
Save-Manifest

$summary = [ordered]@{
    schema_version = 1
    task_name = $script:manifest.task_name
    run_id = $runId
    state = $script:manifest.state
    started_at = $script:manifest.started_at
    finished_at = $script:manifest.finished_at
    pipeline_exit_code = $nightlyExitCode
    supplemental = $script:manifest.supplemental
    manifest_path = $manifestPath
}
Write-JsonAtomic -Path $summaryPath -Payload $summary
Remove-ExpiredSchedulerFiles
exit $nightlyExitCode
