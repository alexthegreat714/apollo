$ErrorActionPreference = "Stop"
if (Get-Variable PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..\..")
$logRoot = Join-Path $repoRoot "Apollo\logs\corpus_refresh"
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
$launcherLog = Join-Path $logRoot ("homework_kg_llm_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")

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
    "[$(Get-Date -Format s)] starting Apollo LLM KG enrichment via $pythonExe" | Tee-Object -FilePath $launcherLog -Append | Out-Null

    $prevErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"

    $apolloChromaDir = Join-Path $repoRoot "Apollo\chroma_db"

    # Run gx10 LLM extraction on up to 600 unindexed chunks.
    # gx10 extracts 3-7 fact triples/chunk vs regex 1-2 triples/chunk.
    # 600 chunks * ~4s/chunk = ~40 min per run; across nights covers full corpus.
    "[$(Get-Date -Format s)] step 1: LLM KG enrichment (gx10, max 600 chunks)" | Tee-Object -FilePath $launcherLog -Append | Out-Null
    & $pythonExe -m Apollo.apollo_hipporag.build_graph `
        --rag-dir $apolloChromaDir `
        --use-llm `
        --llm-only `
        --max-new-chunks 600 `
        *>> $launcherLog
    $llmExit = if ($LASTEXITCODE -ne $null) { [int]$LASTEXITCODE } else { 0 }
    "[$(Get-Date -Format s)] LLM KG enrichment exit=$llmExit" | Tee-Object -FilePath $launcherLog -Append | Out-Null

    $ErrorActionPreference = $prevErrorAction
    $exitCode = $llmExit
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
