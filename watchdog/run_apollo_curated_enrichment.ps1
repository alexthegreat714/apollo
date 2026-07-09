param(
  [string]$RunId = "",
  [string]$RunDate = "",
  [int]$MaxChunks = 120,
  [int]$BatchSize = 20,
  [string]$LlmModel = "qwen3-vl:32b",
  [string]$OllamaUrl = "http://127.0.0.1:11434",
  [int]$LlmTimeoutSeconds = 90,
  [int]$LlmNumPredict = 400,
  [int]$ConfidenceThreshold = 7,
  [string]$CalendarEventId = ""
)

$ErrorActionPreference = "Stop"
if (Get-Variable PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..\..")

if ([string]::IsNullOrWhiteSpace($RunDate)) {
    $RunDate = (Get-Date).ToString("yyyy-MM-dd")
}
if ([string]::IsNullOrWhiteSpace($RunId)) {
    $RunId = "curated_enrichment_$((Get-Date).ToString('yyyyMMdd_HHmmss'))"
}

$logRoot = Join-Path $repoRoot "Apollo\logs\curated_enrichment"
$lockFile = Join-Path $logRoot "curated_enrichment.lock"
$logDir = Join-Path $logRoot "$RunDate\$RunId"
$statusPath = Join-Path $logDir "status.json"
$ragDir = Join-Path $repoRoot "Apollo\chroma_db"
$kgPath = Join-Path $ragDir "financial_kg.json"
$backupKgPath = Join-Path $logDir "financial_kg.before.json"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# ── Lock ──────────────────────────────────────────────────────────────────────
if (Test-Path $lockFile) {
    $lockAge = (Get-Date) - (Get-Item $lockFile).LastWriteTime
    if ($lockAge.TotalHours -lt 6) {
        "[$(Get-Date -Format s)] SKIP: lock file exists (age=$([int]$lockAge.TotalMinutes)min). Another run is active." |
            Tee-Object -FilePath (Join-Path $logDir "skipped.log") -Append | Out-Null
        exit 0
    }
    Remove-Item -Force $lockFile
}
"$RunId" | Set-Content -Path $lockFile -Encoding UTF8

# ── Helpers ───────────────────────────────────────────────────────────────────
function Resolve-Python {
    $candidates = @(
        "C:\Users\blyth\AppData\Local\Programs\Python\Python312\python.exe",
        "C:\Users\blyth\AppData\Local\Programs\Python\Python311\python.exe"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { return $c }
    }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Path) { return $cmd.Path }
    throw "python_not_found"
}

$pythonExe = Resolve-Python

function Write-Status {
    param([hashtable]$Payload)
    if (-not $Payload.ContainsKey("state")) { $Payload["state"] = "running" }
    if (-not $Payload.ContainsKey("message")) { $Payload["message"] = $Payload["state"] }
    $Payload["updated_at"] = (Get-Date).ToUniversalTime().ToString("o")
    $Payload["run_id"] = $RunId
    $Payload["run_date"] = $RunDate
    $Payload["model"] = $LlmModel
    $Payload["max_chunks"] = $MaxChunks
    $Payload["confidence_threshold"] = $ConfidenceThreshold
    $Payload | ConvertTo-Json -Depth 12 | Set-Content -Path $statusPath -Encoding UTF8
}

function Update-SkyCalendar {
    param([string]$Status, [string]$Trigger)
    if ([string]::IsNullOrWhiteSpace($CalendarEventId)) { return }
    try {
        $body = @{
            status = $Status
            trigger = $Trigger
            project = "apollo"
            test_type = "apollo_curated_enrichment"
            run_id = $RunId
            capability = "curated financial KG enrichment"
            report_path = $statusPath
            metadata = @{
                project = "apollo"
                test_type = "apollo_curated_enrichment"
                run_id = $RunId
                capability = "curated financial KG enrichment"
                report_path = $statusPath
                record_path = $statusPath
                apollo_calendar_sync = $true
            }
        } | ConvertTo-Json -Depth 8
        Invoke-RestMethod -Method Patch `
            -Uri "http://127.0.0.1:5011/calendar/events/$CalendarEventId" `
            -ContentType "application/json" -Body $body -TimeoutSec 10 | Out-Null
    } catch {}
}

function Post-SkyCalendar {
    param([string]$Title, [string]$Description, [string]$StartIso, [string]$EndIso)
    try {
        $body = @{
            title = $Title
            start = $StartIso
            end = $EndIso
            status = "in_work"
            source = "apollo"
            trigger = "Apollo curated KG enrichment queued."
            notes = $Description
            project = "apollo"
            test_type = "apollo_curated_enrichment"
            run_id = $RunId
            capability = "curated financial KG enrichment"
            report_path = $statusPath
            metadata = @{
                project = "apollo"
                test_type = "apollo_curated_enrichment"
                run_id = $RunId
                capability = "curated financial KG enrichment"
                report_path = $statusPath
                record_path = $statusPath
                apollo_calendar_sync = $true
            }
        } | ConvertTo-Json -Depth 5
        $resp = Invoke-RestMethod -Method Post `
            -Uri "http://127.0.0.1:5011/calendar/events" `
            -ContentType "application/json" -Body $body -TimeoutSec 10
        $evtId = if ($resp.id) { $resp.id } else { "" }
        return $evtId
    } catch {
        return ""
    }
}

function Invoke-Python {
    param([string]$StepName, [string[]]$Arguments)
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $stdout = Join-Path $logDir "$StepName`_$stamp.stdout.log"
    $stderr = Join-Path $logDir "$StepName`_$stamp.stderr.log"
    $exitCode = 1
    Push-Location $repoRoot
    $prev = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & $pythonExe @Arguments > $stdout 2> $stderr
        $exitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
    } catch {
        $exitCode = 1
        try { Add-Content -Path $stderr -Value $_.Exception.Message -Encoding UTF8 } catch {}
    } finally {
        $ErrorActionPreference = $prev
        Pop-Location
    }
    return [ordered]@{
        step      = $StepName
        exit_code = $exitCode
        ok        = ($exitCode -eq 0)
        stdout    = $stdout
        stderr    = $stderr
        done_at   = (Get-Date).ToUniversalTime().ToString("o")
    }
}

# ── Main ──────────────────────────────────────────────────────────────────────
$exitCode = 1
try {
    [System.Diagnostics.Process]::GetCurrentProcess().PriorityClass =
        [System.Diagnostics.ProcessPriorityClass]::BelowNormal
} catch {}

$env:OMP_NUM_THREADS    = "4"
$env:OPENBLAS_NUM_THREADS = "4"
$env:MKL_NUM_THREADS    = "4"
$env:OLLAMA_URL         = $OllamaUrl
$env:APOLLO_CURATED_LLM_MODEL     = $LlmModel
$env:APOLLO_CURATED_LLM_TIMEOUT_SECS = [string]$LlmTimeoutSeconds
$env:APOLLO_CURATED_CONFIDENCE_THRESHOLD = [string]$ConfidenceThreshold

$startIso = (Get-Date).ToUniversalTime().ToString("o")
$estEndIso = (Get-Date).AddHours(3).ToUniversalTime().ToString("o")

# Create Sky calendar event
if ([string]::IsNullOrWhiteSpace($CalendarEventId)) {
    $CalendarEventId = Post-SkyCalendar `
        -Title "Apollo Curated KG Enrichment ($LlmModel)" `
        -Description "Curated HippoRAG enrichment: $MaxChunks chunks, confidence>=$ConfidenceThreshold, model=$LlmModel. Status path: $statusPath" `
        -StartIso $startIso -EndIso $estEndIso
}

Write-Status @{
    state         = "running"
    message       = "Curated enrichment starting. model=$LlmModel  max=$MaxChunks  threshold=$ConfidenceThreshold"
    current_stage = "init"
    calendar_event_id = $CalendarEventId
}
Update-SkyCalendar -Status "in_work" -Trigger "Apollo curated KG enrichment started (run_id=$RunId, model=$LlmModel)."

# Backup KG
if (Test-Path $kgPath) {
    Copy-Item -LiteralPath $kgPath -Destination $backupKgPath -Force
    "[$(Get-Date -Format s)] KG backed up → $backupKgPath" |
        Add-Content -Path (Join-Path $logDir "watchdog.log") -Encoding UTF8
}

Write-Status @{
    state         = "running"
    message       = "Running curated enrichment via Ollama."
    current_stage = "enrich"
    calendar_event_id = $CalendarEventId
}

$buildArgs = @(
    "-m", "Apollo.apollo_hipporag.curated_enrichment",
    "--rag-dir",            $ragDir,
    "--collection",         "apollo_financial",
    "--max-chunks",         [string]$MaxChunks,
    "--batch-size",         [string]$BatchSize,
    "--model",              $LlmModel,
    "--ollama-url",         $OllamaUrl,
    "--llm-timeout",        [string]$LlmTimeoutSeconds,
    "--llm-num-predict",    [string]$LlmNumPredict,
    "--confidence-threshold", [string]$ConfidenceThreshold,
    "--log-dir",            $logDir
)
$build = Invoke-Python -StepName "curated_enrichment" -Arguments $buildArgs

# Read run_log.json for summary
$runLog = $null
$runLogPath = Join-Path $logDir "run_log.json"
if (Test-Path $runLogPath) {
    try {
        $runLog = Get-Content $runLogPath -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {}
}

if (-not $build.ok) {
    # Restore backup on failure
    if ((Test-Path $backupKgPath) -and (Test-Path $kgPath)) {
        Copy-Item -LiteralPath $backupKgPath -Destination $kgPath -Force
        "[$(Get-Date -Format s)] Build failed; restored KG from backup." |
            Add-Content -Path (Join-Path $logDir "watchdog.log") -Encoding UTF8
    }
    Write-Status @{
        state         = "failed"
        message       = "Curated enrichment failed; KG restored from backup."
        current_stage = "failed_restored"
        build         = $build
        backup_kg_path = $backupKgPath
        calendar_event_id = $CalendarEventId
    }
    Update-SkyCalendar -Status "failed" `
        -Trigger "Apollo curated enrichment FAILED (run_id=$RunId). KG restored. Check $logDir."
    Remove-Item -Force $lockFile -ErrorAction SilentlyContinue
    exit 1
}

# ── Print rich summary from run_log ───────────────────────────────────────────
$summaryLines = @()
$summaryLines += "[$(Get-Date -Format s)] Curated enrichment complete."
if ($runLog) {
    $summaryLines += "  processed=$($runLog.processed)  accepted=$($runLog.accepted)  triples_added=$($runLog.triples_added)"
    $summaryLines += "  rej_confidence=$($runLog.rejected_low_confidence)  rej_dedup=$($runLog.rejected_dedup)"
    $nodesBefore = if ($runLog.kg_before) { $runLog.kg_before.nodes } else { "?" }
    $nodesAfter  = if ($runLog.kg_after)  { $runLog.kg_after.nodes }  else { "?" }
    $edgesBefore = if ($runLog.kg_before) { $runLog.kg_before.edges } else { "?" }
    $edgesAfter  = if ($runLog.kg_after)  { $runLog.kg_after.edges }  else { "?" }
    $summaryLines += "  KG nodes: $nodesBefore -> $nodesAfter (+$($runLog.nodes_delta))"
    $summaryLines += "  KG edges: $edgesBefore -> $edgesAfter (+$($runLog.edges_delta))"
    $summaryLines += "  elapsed=$($runLog.elapsed_sec)s"

    $accSamples = $runLog.sample_accepted
    if ($accSamples -and $accSamples.Count -gt 0) {
        $summaryLines += ""
        $summaryLines += "  ACCEPTED EXAMPLES (triples added):"
        foreach ($ex in $accSamples) {
            $tripleCount = if ($ex.triples_added) { $ex.triples_added.Count } else { 0 }
            $summaryLines += "    doc=$($ex.doc_id)  kind=$($ex.kind)  conf=$($ex.confidence)  triples=$tripleCount"
            if ($ex.triples_added) {
                foreach ($t in $ex.triples_added | Select-Object -First 3) {
                    if ($t -and $t.Count -ge 3) {
                        $summaryLines += "      -> [$($t[0])] --[$($t[1])]--> [$($t[2])]"
                    }
                }
            }
        }
    }

    $rejSamples = $runLog.sample_rejected
    if ($rejSamples -and $rejSamples.Count -gt 0) {
        $summaryLines += ""
        $summaryLines += "  REJECTED EXAMPLES:"
        foreach ($ex in $rejSamples) {
            $summaryLines += "    doc=$($ex.doc_id)  kind=$($ex.kind)  conf=$($ex.confidence)  reason=$($ex.reject_reason)"
        }
    }
}
$summaryLines | ForEach-Object { $_ } |
    Add-Content -Path (Join-Path $logDir "watchdog.log") -Encoding UTF8
$summaryLines | ForEach-Object { Write-Host $_ }

$rlProcessed    = if ($runLog) { $runLog.processed }    else { "?" }
$rlAccepted     = if ($runLog) { $runLog.accepted }     else { "?" }
$rlTriples      = if ($runLog) { $runLog.triples_added } else { "?" }
$rlNodesDelta   = if ($runLog) { $runLog.nodes_delta }  else { "?" }
$rlEdgesDelta   = if ($runLog) { $runLog.edges_delta }  else { "?" }
$rlElapsed      = if ($runLog) { $runLog.elapsed_sec }  else { "?" }

$calTrigger = "Apollo curated enrichment done. " +
    "processed=$rlProcessed  accepted=$rlAccepted  " +
    "triples_added=$rlTriples  run_id=$RunId"

Write-Status @{
    state         = "complete"
    message       = "Curated enrichment complete."
    current_stage = "complete"
    build         = $build
    run_log_path  = $runLogPath
    backup_kg_path = $backupKgPath
    calendar_event_id = $CalendarEventId
    summary       = @{
        processed      = $rlProcessed
        accepted       = $rlAccepted
        triples_added  = $rlTriples
        nodes_delta    = $rlNodesDelta
        edges_delta    = $rlEdgesDelta
        elapsed_sec    = $rlElapsed
    }
}
Update-SkyCalendar -Status "understood" -Trigger $calTrigger

Remove-Item -Force $lockFile -ErrorAction SilentlyContinue
exit 0
