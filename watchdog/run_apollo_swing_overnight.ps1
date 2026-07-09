$ErrorActionPreference = "Stop"
if (Get-Variable PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..\..")
$logRoot = Join-Path $repoRoot "Apollo\logs\swing_study"
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
$launcherLog = Join-Path $logRoot ("overnight_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")

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
    "[$(Get-Date -Format s)] starting Apollo overnight swing study via $pythonExe" | Tee-Object -FilePath $launcherLog -Append | Out-Null

    $prevErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"

    # Run swing study with all data refreshed overnight.
    # Uses: updated KG (fact triples + LLM enrichment), fresh earnings dates,
    # sector ETF RS, and updated market/swing snapshots.
    "[$(Get-Date -Format s)] step 1: swing study" | Tee-Object -FilePath $launcherLog -Append | Out-Null
    & $pythonExe -m Apollo.swing_study *>> $launcherLog
    $swingExit = if ($LASTEXITCODE -ne $null) { [int]$LASTEXITCODE } else { 0 }
    "[$(Get-Date -Format s)] swing study exit=$swingExit" | Tee-Object -FilePath $launcherLog -Append | Out-Null

    $ErrorActionPreference = $prevErrorAction
    $exitCode = $swingExit
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
