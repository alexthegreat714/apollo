$ErrorActionPreference = "Stop"
if (Get-Variable PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..\..")
$logRoot = Join-Path $repoRoot "Apollo\logs\corpus_refresh"
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
$launcherLog = Join-Path $logRoot ("homework_10k_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")

function Resolve-Python {
    $candidates = @(
        "C:\Users\blyth\AppData\Local\Programs\Python\Python312\python.exe",
        "C:\Users\blyth\AppData\Local\Programs\Python\Python311\python.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return $candidate }
    }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Path) { return $cmd.Path }
    throw "python_not_found"
}

$pythonExe = Resolve-Python
$exitCode = 1

function Stop-ProcessTree {
    param([int]$ProcessId)
    if ($ProcessId -le 0) { return }
    try {
        & "$env:SystemRoot\System32\taskkill.exe" /PID $ProcessId /T /F | Out-Null
    } catch {}
}

function Invoke-PythonStep {
    param(
        [string]$StepName,
        [string[]]$Arguments,
        [int]$TimeoutSeconds = 1800
    )
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $stdout = Join-Path $logRoot "$StepName`_$stamp.stdout.log"
    $stderr = Join-Path $logRoot "$StepName`_$stamp.stderr.log"
    $argumentText = ($Arguments | ForEach-Object {
        if ($_ -match '\s') { '"' + $_.Replace('"', '\"') + '"' } else { $_ }
    }) -join ' '
    $proc = Start-Process -FilePath $pythonExe `
        -ArgumentList $argumentText `
        -WorkingDirectory $repoRoot `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -PassThru
    $timedOut = $false
    try {
        Wait-Process -Id $proc.Id -Timeout $TimeoutSeconds -ErrorAction Stop
    } catch {
        $timedOut = $true
        Stop-ProcessTree -ProcessId $proc.Id
    }
    $proc.Refresh()
    $result = [ordered]@{
        exit_code = if ($timedOut) { 124 } elseif ($null -ne $proc.ExitCode) { [int]$proc.ExitCode } else { 1 }
        timed_out = $timedOut
        stdout = $stdout
        stderr = $stderr
    }
    return $result
}

function Read-JsonObject {
    param([string]$Path)
    if (-not $Path -or -not (Test-Path $Path)) { return $null }
    try {
        $raw = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
        if (-not $raw) { return $null }
        return $raw | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Resolve-IngestExitCode {
    param(
        [int]$ExitCode,
        [string]$StdoutPath
    )
    if ($ExitCode -eq 0) {
        return 0
    }
    $payload = Read-JsonObject -Path $StdoutPath
    if ($null -eq $payload) {
        return $ExitCode
    }
    $ok = [bool]($payload.ok)
    $totalChunks = 0
    $edgarErrors = 0
    try { $totalChunks = [int]($payload.total_chunks_added) } catch {}
    try { $edgarErrors = [int]($payload.edgar_errors) } catch {}
    if ($ok -and $totalChunks -eq 0 -and $edgarErrors -eq 0) {
        "[$(Get-Date -Format s)] deep ingest returned nonzero but reported ok=true with no new 10-K filings; treating as benign no-op success" | Tee-Object -FilePath $launcherLog -Append | Out-Null
        return 0
    }
    return $ExitCode
}

Push-Location $repoRoot
try {
    "[$(Get-Date -Format s)] starting Apollo 10-K deep ingest via $pythonExe" | Tee-Object -FilePath $launcherLog -Append | Out-Null

    $prevErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $ingestTimeoutSec = 1800
    $graphTimeoutSec = 900

    # Step 1: ingest 10-K annual reports (richest MD&A narrative text)
    "[$(Get-Date -Format s)] step 1: 10-K deep ingest" | Tee-Object -FilePath $launcherLog -Append | Out-Null
    $ingest = Invoke-PythonStep -StepName "homework_10k_ingest" -Arguments @("-m", "Apollo.corpus_live_ingest", "deep") -TimeoutSeconds $ingestTimeoutSec
    $ingestExit = Resolve-IngestExitCode -ExitCode ([int]$ingest.exit_code) -StdoutPath $ingest.stdout
    if ($ingest.timed_out) {
        "[$(Get-Date -Format s)] 10-K ingest TIMED OUT after $ingestTimeoutSec sec" | Tee-Object -FilePath $launcherLog -Append | Out-Null
    }
    "[$(Get-Date -Format s)] 10-K ingest exit=$ingestExit stdout=$($ingest.stdout) stderr=$($ingest.stderr)" | Tee-Object -FilePath $launcherLog -Append | Out-Null

    # Step 2: incremental KG rebuild from newly ingested 10-K chunks (regex only, fast)
    $graphExit = 1
    if ($ingestExit -eq 0) {
        "[$(Get-Date -Format s)] step 2: incremental KG rebuild (regex)" | Tee-Object -FilePath $launcherLog -Append | Out-Null
        $apolloChromaDir = Join-Path $repoRoot "Apollo\chroma_db"
        $graph = Invoke-PythonStep -StepName "homework_10k_graph" -Arguments @("-m", "Apollo.apollo_hipporag.build_graph", "--rag-dir", $apolloChromaDir, "--max-new-chunks", "3000") -TimeoutSeconds $graphTimeoutSec
        $graphExit = [int]$graph.exit_code
        if ($graph.timed_out) {
            "[$(Get-Date -Format s)] KG rebuild TIMED OUT after $graphTimeoutSec sec" | Tee-Object -FilePath $launcherLog -Append | Out-Null
        }
        "[$(Get-Date -Format s)] KG rebuild exit=$graphExit stdout=$($graph.stdout) stderr=$($graph.stderr)" | Tee-Object -FilePath $launcherLog -Append | Out-Null
    } else {
        "[$(Get-Date -Format s)] skipping KG rebuild because 10-K ingest did not finish cleanly" | Tee-Object -FilePath $launcherLog -Append | Out-Null
    }

    $ErrorActionPreference = $prevErrorAction
    $exitCode = if ($ingestExit -eq 0) { 0 } else { 1 }
}
catch {
    "[$(Get-Date -Format s)] launcher error: $($_)" | Tee-Object -FilePath $launcherLog -Append | Out-Null
    $exitCode = 1
}
finally {
    "[$(Get-Date -Format s)] finished exit=$exitCode" | Tee-Object -FilePath $launcherLog -Append | Out-Null
    Pop-Location
}
exit $exitCode
