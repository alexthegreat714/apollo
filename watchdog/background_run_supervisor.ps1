param(
    [int]$DurationMinutes = 240,
    [int]$PollSeconds = 45,
    [string]$BaseUrl = "http://127.0.0.1:5218",
    [string]$LogPath = ""
)

$ErrorActionPreference = "Stop"

$apolloRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$defaultLogDir = Join-Path $apolloRoot "logs\\nightly"
if (-not (Test-Path $defaultLogDir)) {
    New-Item -ItemType Directory -Path $defaultLogDir -Force | Out-Null
}
if ([string]::IsNullOrWhiteSpace($LogPath)) {
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $LogPath = Join-Path $defaultLogDir ("background_supervisor_{0}.log" -f $stamp)
}

function Write-Log {
    param(
        [string]$Message,
        [string]$Level = "INFO"
    )
    $line = "{0} [{1}] {2}" -f (Get-Date -Format "yyyy-MM-ddTHH:mm:ssK"), $Level, $Message
    Add-Content -Path $LogPath -Value $line
}

function Invoke-JsonGet {
    param([string]$Path)
    try {
        return Invoke-RestMethod -Method Get -Uri ("{0}{1}" -f $BaseUrl, $Path) -TimeoutSec 20
    } catch {
        Write-Log ("GET {0} failed: {1}" -f $Path, $_.Exception.Message) "WARN"
        return $null
    }
}

function Invoke-JsonPost {
    param(
        [string]$Path,
        [hashtable]$Body
    )
    try {
        $json = ($Body | ConvertTo-Json -Depth 20)
        return Invoke-RestMethod -Method Post -Uri ("{0}{1}" -f $BaseUrl, $Path) -ContentType "application/json" -Body $json -TimeoutSec 30
    } catch {
        Write-Log ("POST {0} failed: {1}" -f $Path, $_.Exception.Message) "WARN"
        return $null
    }
}

function Wait-For-Health {
    param([int]$MaxSeconds = 120)
    $deadline = (Get-Date).AddSeconds($MaxSeconds)
    while ((Get-Date) -lt $deadline) {
        $health = Invoke-JsonGet "/health"
        if ($null -ne $health -and [string]$health.status -eq "ok") {
            return $true
        }
        Start-Sleep -Seconds 4
    }
    return $false
}

function Restart-Apollo {
    $restartBat = Join-Path $apolloRoot "watchdog\\restart_apollo_headless.bat"
    if (-not (Test-Path $restartBat)) {
        Write-Log ("restart script missing: {0}" -f $restartBat) "ERROR"
        return $false
    }
    Write-Log "Restarting Apollo headless service"
    try {
        $proc = Start-Process -FilePath "cmd.exe" -ArgumentList @("/c", $restartBat) -WindowStyle Hidden -PassThru -Wait
        Write-Log ("Restart command exit={0}" -f $proc.ExitCode)
    } catch {
        Write-Log ("Restart failed: {0}" -f $_.Exception.Message) "ERROR"
        return $false
    }
    $healthy = Wait-For-Health -MaxSeconds 120
    if (-not $healthy) {
        Write-Log "Apollo did not return healthy in time after restart" "ERROR"
    }
    return $healthy
}

$runPayload = @{
    ocr_scan_min_structure_score = 0.10
    ocr_scan_min_numeric_fidelity = 0.10
    ocr_scan_min_anchor_hits = 0
    ocr_min_structure_score = 0.10
    ocr_min_numeric_fidelity = 0.10
    ocr_min_non_scrape_ratio = 0.50
    ocr_structure_bench_min = 10
    ocr_correctness_bench_min = 10
    ocr_doc_min_chars = 400
    ocr_avg_min_chars = 1000
    ocr_stage_min_score = 60
    strict_stage_min_score = 60
    background_ocr_docs_per_tick = 2
    ocr_scan_min_quality_score = 30
}

Write-Log ("Supervisor start duration={0}m poll={1}s base={2}" -f $DurationMinutes, $PollSeconds, $BaseUrl)
if (-not (Wait-For-Health -MaxSeconds 30)) {
    Write-Log "Initial health check failed; attempting restart" "WARN"
    Restart-Apollo | Out-Null
}

$kick = Invoke-JsonPost -Path "/admin/background_pipeline/run" -Body $runPayload
if ($null -ne $kick) {
    Write-Log ("Initial run trigger ok={0} accepted={1} err={2}" -f $kick.ok, $kick.accepted, $kick.error)
}

$deadline = (Get-Date).AddMinutes($DurationMinutes)
$lastSeq = -1
$lastStage = ""
$stallCount = 0
$healthFailCount = 0
$ocrNoDocsCount = 0
$lockWaitCount = 0
$rerunAttempts = 0
$maxRerunAttempts = 2

while ((Get-Date) -lt $deadline) {
    $statusResp = Invoke-JsonGet "/admin/background_pipeline/status"
    if ($null -eq $statusResp -or $null -eq $statusResp.status) {
        $healthFailCount += 1
        Write-Log ("Status read failed count={0}" -f $healthFailCount) "WARN"
        if ($healthFailCount -ge 2) {
            if (Restart-Apollo) {
                $healthFailCount = 0
                Start-Sleep -Seconds 3
                Invoke-JsonPost -Path "/admin/background_pipeline/run" -Body $runPayload | Out-Null
            }
        }
        Start-Sleep -Seconds ([Math]::Max(5, $PollSeconds))
        continue
    }
    $healthFailCount = 0

    $status = $statusResp.status
    $stage = [string]($status.current_stage)
    $seq = 0
    try {
        $seq = [int]($status.status_seq)
    } catch {
        $seq = 0
    }
    $waitingReason = [string](($status.waiting).reason)
    $fails = @()
    if ($null -ne $status.quality -and $null -ne $status.quality.gate_fail_reasons) {
        $fails = @($status.quality.gate_fail_reasons)
    }

    Write-Log ("stage={0} seq={1} waiting={2} overall={3} gate={4}" -f $stage, $seq, $waitingReason, ($status.quality.overall_score), ($status.quality.gate_pass))

    if ($stage -eq "done") {
        $gatePass = [bool]($status.quality.gate_pass)
        if ($gatePass) {
            Write-Log "Pipeline reached done stage with gate_pass=true; supervisor exiting"
            break
        }
        $hasDoneOcrNoDocs = $false
        foreach ($item in $fails) {
            if ([string]$item -like "*ocr_no_documents*") {
                $hasDoneOcrNoDocs = $true
                break
            }
        }
        if ($hasDoneOcrNoDocs -and $rerunAttempts -lt $maxRerunAttempts) {
            $rerunAttempts += 1
            Write-Log ("Done but gate failed on OCR (attempt {0}/{1}); stop/reset/resume/rerun" -f $rerunAttempts, $maxRerunAttempts) "WARN"
            Invoke-JsonPost -Path "/admin/background_pipeline/stop" -Body @{ reset = $true } | Out-Null
            Start-Sleep -Seconds 2
            Invoke-JsonPost -Path "/admin/background_pipeline/resume" -Body @{} | Out-Null
            Start-Sleep -Seconds 2
            Invoke-JsonPost -Path "/admin/background_pipeline/run" -Body $runPayload | Out-Null
            $stallCount = 0
            $ocrNoDocsCount = 0
            $lockWaitCount = 0
            Start-Sleep -Seconds ([Math]::Max(5, $PollSeconds))
            continue
        }
        Write-Log "Pipeline reached done stage with gate failure; max reruns reached or non-OCR failure. Exiting." "WARN"
        break
    }

    if ($waitingReason -eq "paused" -or $waitingReason -eq "stopped") {
        Write-Log ("Control state waiting={0}; issuing resume" -f $waitingReason) "WARN"
        Invoke-JsonPost -Path "/admin/background_pipeline/resume" -Body @{} | Out-Null
        Start-Sleep -Seconds 2
        Invoke-JsonPost -Path "/admin/background_pipeline/run" -Body $runPayload | Out-Null
    }

    if ($seq -eq $lastSeq -and $stage -eq $lastStage) {
        $stallCount += 1
    } else {
        $stallCount = 0
        $lastSeq = $seq
        $lastStage = $stage
    }

    $hasOcrNoDocs = $false
    foreach ($item in $fails) {
        if ([string]$item -like "*ocr_no_documents*") {
            $hasOcrNoDocs = $true
            break
        }
    }
    if ($hasOcrNoDocs) {
        $ocrNoDocsCount += 1
    } else {
        $ocrNoDocsCount = 0
    }

    if ([string]$waitingReason -like "*nightly_lock_present*") {
        $lockWaitCount += 1
    } else {
        $lockWaitCount = 0
    }

    if ($lockWaitCount -ge 3) {
        Write-Log "Repeated nightly_lock_present loop; stop/reset/resume/rerun" "WARN"
        Invoke-JsonPost -Path "/admin/background_pipeline/stop" -Body @{ reset = $true } | Out-Null
        Start-Sleep -Seconds 2
        Invoke-JsonPost -Path "/admin/background_pipeline/resume" -Body @{} | Out-Null
        Start-Sleep -Seconds 2
        Invoke-JsonPost -Path "/admin/background_pipeline/run" -Body $runPayload | Out-Null
        $lockWaitCount = 0
        $stallCount = 0
        Start-Sleep -Seconds ([Math]::Max(5, $PollSeconds))
        continue
    }

    if ($ocrNoDocsCount -ge 3) {
        Write-Log "Repeated ocr_no_documents loop; stop/reset/resume/rerun with relaxed OCR thresholds" "WARN"
        Invoke-JsonPost -Path "/admin/background_pipeline/stop" -Body @{ reset = $true } | Out-Null
        Start-Sleep -Seconds 2
        Invoke-JsonPost -Path "/admin/background_pipeline/resume" -Body @{} | Out-Null
        Start-Sleep -Seconds 2
        Invoke-JsonPost -Path "/admin/background_pipeline/run" -Body $runPayload | Out-Null
        $ocrNoDocsCount = 0
        $stallCount = 0
        Start-Sleep -Seconds ([Math]::Max(5, $PollSeconds))
        continue
    }

    if ($stallCount -ge 4) {
        Write-Log ("Detected stall stage={0} seq={1}; issuing run-step nudge" -f $stage, $seq) "WARN"
        Invoke-JsonPost -Path "/admin/background_pipeline/run" -Body $runPayload | Out-Null
        $stallCount = 0
    }

    Start-Sleep -Seconds ([Math]::Max(5, $PollSeconds))
}

Write-Log "Supervisor finished"
