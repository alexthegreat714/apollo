$ErrorActionPreference = "Stop"
if (Get-Variable PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..\..")
$logRoot = Join-Path $repoRoot "Apollo\logs\corpus_refresh"
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
$launcherLog = Join-Path $logRoot ("launcher_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")

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
Push-Location $repoRoot
try {
    "[$(Get-Date -Format s)] starting Apollo corpus refresh via $pythonExe" | Tee-Object -FilePath $launcherLog -Append | Out-Null

    $prevErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"

    # Step 1: live ingest (EDGAR filings + Yahoo Finance RSS for all 36 tickers)
    "[$(Get-Date -Format s)] step 1: live ingest" | Tee-Object -FilePath $launcherLog -Append | Out-Null
    & $pythonExe -m Apollo.corpus_live_ingest *>> $launcherLog
    $ingestExit = if ($LASTEXITCODE -ne $null) { [int]$LASTEXITCODE } else { 0 }
    "[$(Get-Date -Format s)] live ingest exit=$ingestExit" | Tee-Object -FilePath $launcherLog -Append | Out-Null

    # Step 2: rebuild the financial knowledge graph from the updated corpus
    "[$(Get-Date -Format s)] step 2: build_graph" | Tee-Object -FilePath $launcherLog -Append | Out-Null
    $apolloChromaDir = Join-Path $repoRoot "Apollo\chroma_db"
    & $pythonExe -m Apollo.apollo_hipporag.build_graph --rag-dir $apolloChromaDir *>> $launcherLog
    $graphExit = if ($LASTEXITCODE -ne $null) { [int]$LASTEXITCODE } else { 0 }
    "[$(Get-Date -Format s)] build_graph exit=$graphExit" | Tee-Object -FilePath $launcherLog -Append | Out-Null

    # Step 3: write stage report for Sky morning report
    "[$(Get-Date -Format s)] step 3: finalize_corpus_report ingest=$ingestExit graph=$graphExit" | Tee-Object -FilePath $launcherLog -Append | Out-Null
    & $pythonExe -m Apollo.corpus_live_ingest report $ingestExit $graphExit *>> $launcherLog
    "[$(Get-Date -Format s)] finalize_corpus_report done" | Tee-Object -FilePath $launcherLog -Append | Out-Null

    $ErrorActionPreference = $prevErrorAction
    $exitCode = if ($ingestExit -eq 0 -and $graphExit -eq 0) { 0 } else { 1 }
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
